import json
import os
import subprocess
import sys
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from typing import List, Dict, Any, Optional, Tuple, Set

ORG_ID = "655586926911"
MAX_WORKERS = 10
FORWARDING_RULE_SCHEMES = {"EXTERNAL", "EXTERNAL_MANAGED", "INTERNAL", "INTERNAL_MANAGED"}
GCLOUD_ENV = {**os.environ, "CLOUDSDK_CORE_DISABLE_PROMPTS": "1"}

TARGET_CMD_MAP = {
    "targetHttpProxies": "target-http-proxies",
    "targetHttpsProxies": "target-https-proxies",
    "targetGrpcProxies": "target-grpc-proxies",
    "targetTcpProxies": "target-tcp-proxies",
    "targetSslProxies": "target-ssl-proxies",
}

RESOURCE_CMD_MAP = {
    "urlMaps": "url-maps",
    "backendServices": "backend-services",
    "networkEndpointGroups": "network-endpoint-groups",
    "targetPools": "target-pools",
    "serviceAttachments": "service-attachments",
}

RETRY_KEYWORDS = [
    "rate limit",
    "quota",
    "429",
    "resource exhausted",
    "backend error",
    "internal error",
    "unavailable",
]


class CommandError(Exception):
    def __init__(self, message: str, stderr: str = "", returncode: int = 1):
        super().__init__(message)
        self.stderr = stderr
        self.returncode = returncode


def run_cmd(cmd: List[str], timeout: int = 120, retries: int = 4, backoff_base: float = 1.6) -> str:
    delay = 2.0
    attempt = 0
    last_err = None
    while attempt < retries:
        attempt += 1
        try:
            completed = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
                env=GCLOUD_ENV,
            )
        except subprocess.TimeoutExpired:
            last_err = f"timeout after {timeout}s"
            if attempt < retries:
                time.sleep(delay + random.uniform(0, 1.0))
                delay *= backoff_base
                continue
            raise CommandError(f"Command timed out: {' '.join(cmd)}", stderr=last_err)

        stdout = completed.stdout
        stderr = completed.stderr
        rc = completed.returncode

        if rc == 0:
            return stdout

        err_text = (stderr or stdout).strip()
        last_err = err_text
        retryable = any(keyword in err_text.lower() for keyword in RETRY_KEYWORDS)
        if retryable and attempt < retries:
            time.sleep(delay + random.uniform(0, 1.0))
            delay *= backoff_base
            continue
        raise CommandError(f"Command failed: {' '.join(cmd)}", stderr=err_text, returncode=rc)

    raise CommandError(f"Command ultimately failed: {' '.join(cmd)}", stderr=last_err or "unknown error")


def parse_json(output: str) -> Any:
    output = output.strip()
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise CommandError(f"JSON parse error: {exc}")


def parse_self_link(link: str) -> Dict[str, Optional[str]]:
    parts = [p for p in link.split('/') if p]
    result = {
        "project": None,
        "type": None,
        "name": None,
        "scope": None,
        "region": None,
        "zone": None,
    }
    if not parts:
        return result
    try:
        proj_idx = parts.index("projects")
        result["project"] = parts[proj_idx + 1]
    except ValueError:
        pass

    if "global" in parts:
        idx = parts.index("global")
        result["scope"] = "global"
        if idx + 1 < len(parts):
            result["type"] = parts[idx + 1]
        if idx + 2 < len(parts):
            result["name"] = parts[idx + 2]
    elif "regions" in parts:
        idx = parts.index("regions")
        result["scope"] = "regional"
        if idx + 1 < len(parts):
            result["region"] = parts[idx + 1]
        if idx + 2 < len(parts):
            result["type"] = parts[idx + 2]
        if idx + 3 < len(parts):
            result["name"] = parts[idx + 3]
    elif "zones" in parts:
        idx = parts.index("zones")
        result["scope"] = "zonal"
        if idx + 1 < len(parts):
            result["zone"] = parts[idx + 1]
        if idx + 2 < len(parts):
            result["type"] = parts[idx + 2]
        if idx + 3 < len(parts):
            result["name"] = parts[idx + 3]
    return result


def summarize_error(project_id: str, err: str) -> str:
    err_lower = err.lower()
    if "service_disabled" in err_lower or "compute engine api has not been used" in err_lower:
        return f"{project_id}: compute.googleapis.com disabled"
    if "access denied" in err_lower or "permission" in err_lower:
        return f"{project_id}: permission denied"
    if "timeout" in err_lower:
        return f"{project_id}: command timeout"
    if err:
        first_line = err.splitlines()[0]
        return f"{project_id}: {first_line}"
    return f"{project_id}: unknown error"


def load_projects() -> List[Dict[str, Any]]:
    cmd = [
        "gcloud",
        "projects",
        "list",
        f"--filter=parent.type:organization AND parent.id={ORG_ID} AND lifecycleState=ACTIVE",
        "--format=json",
        "--quiet",
    ]
    stdout = run_cmd(cmd, timeout=180)
    data = parse_json(stdout)
    return data or []


def list_forwarding_rules(project_id: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    cmd = [
        "gcloud",
        "compute",
        "forwarding-rules",
        "list",
        f"--project={project_id}",
        "--format=json",
        "--quiet",
    ]
    try:
        stdout = run_cmd(cmd, timeout=180)
        rules = parse_json(stdout) or []
        return rules, None
    except CommandError as exc:
        return [], summarize_error(project_id, exc.stderr or str(exc))


def describe_resource(cmd: List[str], project_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        stdout = run_cmd(cmd + ["--format=json", "--quiet"], timeout=180)
        return parse_json(stdout), None
    except CommandError as exc:
        return None, summarize_error(project_id, exc.stderr or str(exc))


def classify_http_type(scheme: str) -> str:
    if scheme in {"EXTERNAL", "EXTERNAL_MANAGED"}:
        return "External HTTP(S) load balancers"
    return "Internal HTTP(S) load balancers"


def classify_lb(rule: Dict[str, Any]) -> Tuple[str, str]:
    scheme = rule.get("loadBalancingScheme")
    target = rule.get("target")
    if target:
        meta = parse_self_link(target)
        t_type = meta.get("type") or ""
        if t_type in {"targetHttpProxies", "targetHttpsProxies", "targetGrpcProxies"}:
            lb_type = classify_http_type(scheme)
            proto = "grpc" if t_type == "targetGrpcProxies" else "https" if "Https" in t_type else "http"
            return lb_type, proto
        if t_type == "targetTcpProxies":
            return "External TCP Proxy load balancers", "tcp"
        if t_type == "targetSslProxies":
            return "External SSL Proxy load balancers", "ssl"
        if t_type == "serviceAttachments":
            return "Private Service Connect (PSC)", "psc"
        if t_type == "targetPools":
            if scheme in {"INTERNAL", "INTERNAL_MANAGED"}:
                return "Internal TCP/UDP load balancers", "tcpudp"
            return "External Network TCP/UDP load balancers", "tcpudp"
    backend_service = rule.get("backendService")
    if backend_service:
        if scheme in {"EXTERNAL", "EXTERNAL_MANAGED"}:
            return "External Network TCP/UDP load balancers", "tcpudp"
        return "Internal TCP/UDP load balancers", "tcpudp"
    if rule.get("serviceClass") or rule.get("serviceLabel") or rule.get("pscConnectionId"):
        return "Private Service Connect (PSC)", "psc"
    return "Unknown", "unknown"


def lb_primary_key(rule: Dict[str, Any]) -> str:
    for field in ("target", "backendService"):
        if rule.get(field):
            return rule[field]
    return rule.get("selfLink", f"forwardingRule:{rule.get('name')}")


def collect_backend_services_from_urlmap(urlmap: Dict[str, Any]) -> Set[str]:
    services: Set[str] = set()
    if urlmap.get("defaultService"):
        services.add(urlmap["defaultService"])
    for matcher in urlmap.get("pathMatchers", []) or []:
        if matcher.get("defaultService"):
            services.add(matcher["defaultService"])
        for path_rule in matcher.get("pathRules", []) or []:
            svc = path_rule.get("service")
            if svc:
                services.add(svc)
        for route_rule in matcher.get("routeRules", []) or []:
            svc = route_rule.get("service")
            if svc:
                services.add(svc)
            for weighted in route_rule.get("weightedBackendServices", []) or []:
                svc = weighted.get("backendService")
                if svc:
                    services.add(svc)
    return services


def convert_labels(resource: Dict[str, Any]) -> Dict[str, str]:
    labels = resource.get("labels")
    if isinstance(labels, dict):
        return {k: str(v) for k, v in labels.items()}
    return {}


def describe_target(target_link: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    meta = parse_self_link(target_link)
    cmd_name = TARGET_CMD_MAP.get(meta.get("type"))
    if not cmd_name:
        return None, f"Unsupported target type for {target_link}"
    cmd = [
        "gcloud",
        "compute",
        cmd_name,
        "describe",
        meta.get("name"),
        f"--project={meta.get('project')}",
    ]
    if meta.get("scope") == "global":
        cmd.append("--global")
    elif meta.get("scope") == "regional":
        cmd.append(f"--region={meta.get('region')}")
    return describe_resource(cmd, meta.get("project"))


def describe_generic(resource_link: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    meta = parse_self_link(resource_link)
    cmd_segment = RESOURCE_CMD_MAP.get(meta.get("type"))
    if not cmd_segment or not meta.get("name"):
        return None, f"Unsupported resource for {resource_link}"
    cmd = [
        "gcloud",
        "compute",
        cmd_segment,
        "describe",
        meta.get("name"),
        f"--project={meta.get('project')}",
    ]
    scope = meta.get("scope")
    if scope == "global":
        cmd.append("--global")
    elif scope == "regional" and meta.get("region"):
        cmd.append(f"--region={meta.get('region')}")
    elif scope == "zonal" and meta.get("zone"):
        cmd.append(f"--zone={meta.get('zone')}")
    return describe_resource(cmd, meta.get("project"))


def main():
    output = {
        "summary": {
            "total": 0,
            "countsByType": defaultdict(int),
            "serverlessCount": 0,
        },
        "loadBalancers": [],
        "warnings": [],
        "projectCount": 0,
        "projectsProcessed": 0,
    }

    projects = load_projects()
    project_ids = [p["projectId"] for p in projects]
    output["projectCount"] = len(project_ids)

    forwarding_rules: List[Dict[str, Any]] = []
    warnings: List[str] = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(list_forwarding_rules, pid): pid for pid in project_ids}
        for future in as_completed(futures):
            pid = futures[future]
            try:
                rules, warn = future.result()
                if warn:
                    warnings.append(warn)
                    continue
                output["projectsProcessed"] += 1
                for rule in rules:
                    if rule.get("loadBalancingScheme") not in FORWARDING_RULE_SCHEMES:
                        continue
                    if rule.get("disabled") is True:
                        continue
                    rule["_projectId"] = pid
                    forwarding_rules.append(rule)
            except Exception as exc:  # pylint: disable=broad-except
                warnings.append(f"{pid}: {exc}")

    lb_map: Dict[str, Dict[str, Any]] = {}

    for rule in forwarding_rules:
        scheme = rule.get("loadBalancingScheme")
        lb_type, proto_note = classify_lb(rule)
        key = lb_primary_key(rule)
        scope = "global" if not rule.get("region") else "regional"
        region = None
        if rule.get("region"):
            region = rule["region"].split("/")[-1]
        lb_entry = lb_map.setdefault(key, {
            "projectId": rule.get("_projectId"),
            "type": lb_type,
            "scheme": scheme,
            "scope": scope,
            "region": region,
            "ipAddresses": set(),
            "ports": set(),
            "protocols": set(),
            "forwardingRules": [],
            "labels": {},
            "creationTimestamp": rule.get("creationTimestamp"),
            "selfLink": None,
            "id": None,
            "targetProxy": None,
            "urlMapLink": None,
            "backendServiceLinks": set(),
            "backendTypes": set(),
            "backendCount": 0,
            "healthChecks": set(),
            "networks": set(),
            "subnetworks": set(),
            "protocolNotes": set(),
            "serverlessBackends": set(),
        })

        if rule.get("IPAddress"):
            lb_entry["ipAddresses"].add(rule["IPAddress"])
        if rule.get("portRange"):
            lb_entry["ports"].add(rule["portRange"])
        if rule.get("ports"):
            for p in rule["ports"]:
                lb_entry["ports"].add(str(p))
        if rule.get("IPProtocol"):
            lb_entry["protocols"].add(rule["IPProtocol"])
        if rule.get("network"):
            lb_entry["networks"].add(rule["network"])
        if rule.get("subnetwork"):
            lb_entry["subnetworks"].add(rule["subnetwork"])
        lb_entry["forwardingRules"].append(
            {
                "name": rule.get("name"),
                "scope": scope,
                "region": region,
                "selfLink": rule.get("selfLink"),
            }
        )
        if not lb_entry["labels"]:
            lb_entry["labels"] = convert_labels(rule)
        else:
            lb_entry["labels"].update(convert_labels(rule))
        lb_entry["protocolNotes"].add(proto_note)
        lb_entry["scheme"] = scheme or lb_entry["scheme"]
        if rule.get("creationTimestamp"):
            existing_ts = lb_entry["creationTimestamp"]
            if not existing_ts or rule["creationTimestamp"] < existing_ts:
                lb_entry["creationTimestamp"] = rule["creationTimestamp"]
        if rule.get("target"):
            lb_entry["primaryTarget"] = rule["target"]
        if rule.get("backendService"):
            lb_entry["backendServiceLinks"].add(rule["backendService"])
        if rule.get("serviceDirectoryRegistrations"):
            lb_entry.setdefault("pscDetails", {})["serviceDirectoryRegistrations"] = rule["serviceDirectoryRegistrations"]
        if rule.get("pscConnectionId"):
            lb_entry.setdefault("pscDetails", {})["pscConnectionId"] = rule["pscConnectionId"]
        if rule.get("pscConnectionStatus"):
            lb_entry.setdefault("pscDetails", {})["pscConnectionStatus"] = rule["pscConnectionStatus"]
        if rule.get("networkTier"):
            lb_entry.setdefault("networkTier", rule["networkTier"])

    target_details: Dict[str, Dict[str, Any]] = {}
    urlmap_details: Dict[str, Dict[str, Any]] = {}
    backend_details: Dict[str, Dict[str, Any]] = {}
    neg_details: Dict[str, Dict[str, Any]] = {}
    target_pool_details: Dict[str, Dict[str, Any]] = {}
    service_attachment_details: Dict[str, Dict[str, Any]] = {}

    for key, lb_entry in lb_map.items():
        target_link = lb_entry.get("primaryTarget")
        if target_link and target_link not in target_details:
            detail, err = describe_target(target_link)
            if err:
                warnings.append(err)
            elif detail:
                target_details[target_link] = detail
                if detail.get("urlMap"):
                    lb_entry["urlMapLink"] = detail.get("urlMap")
        for backend_link in list(lb_entry["backendServiceLinks"]):
            if backend_link not in backend_details:
                detail, err = describe_generic(backend_link)
                if err:
                    warnings.append(err)
                elif detail:
                    backend_details[backend_link] = detail
        if target_link and "serviceAttachments" in (parse_self_link(target_link).get("type") or ""):
            if target_link not in service_attachment_details:
                detail, err = describe_generic(target_link)
                if err:
                    warnings.append(err)
                elif detail:
                    service_attachment_details[target_link] = detail

    for lb_entry in lb_map.values():
        url_map_link = lb_entry.get("urlMapLink")
        if url_map_link and url_map_link not in urlmap_details:
            detail, err = describe_generic(url_map_link)
            if err:
                warnings.append(err)
            elif detail:
                urlmap_details[url_map_link] = detail
                lb_entry["backendServiceLinks"].update(collect_backend_services_from_urlmap(detail))

    for lb_entry in lb_map.values():
        for backend_link in list(lb_entry["backendServiceLinks"]):
            detail = backend_details.get(backend_link)
            if not detail:
                detail, err = describe_generic(backend_link)
                if err:
                    warnings.append(err)
                elif detail:
                    backend_details[backend_link] = detail
                else:
                    continue
            for backend in detail.get("backends", []) or []:
                group = backend.get("group")
                if not group:
                    continue
                gmeta = parse_self_link(group)
                if gmeta.get("type") == "networkEndpointGroups" and group not in neg_details:
                    gdetail, gerr = describe_generic(group)
                    if gerr:
                        warnings.append(gerr)
                    elif gdetail:
                        neg_details[group] = gdetail
            for hc in detail.get("healthChecks", []) or []:
                lb_entry["healthChecks"].add(parse_self_link(hc).get("name") or hc)

    for key, lb_entry in lb_map.items():
        psc_detail = service_attachment_details.get(lb_entry.get("primaryTarget"))
        if psc_detail:
            lb_entry.setdefault("pscDetails", {})["connectionPreference"] = psc_detail.get("connectionPreference")
            if psc_detail.get("targetService"):
                lb_entry["pscDetails"]["targetService"] = psc_detail.get("targetService")

    serverless_lb_count = 0
    lb_records = []

    type_order = {
        "External HTTP(S) load balancers": 1,
        "Internal HTTP(S) load balancers": 2,
        "External TCP Proxy load balancers": 3,
        "External SSL Proxy load balancers": 4,
        "External Network TCP/UDP load balancers": 5,
        "Internal TCP/UDP load balancers": 6,
        "Private Service Connect (PSC)": 7,
        "Unknown": 99,
    }

    for key, lb_entry in lb_map.items():
        backend_names: List[str] = []
        backend_types: Set[str] = set()
        backend_count = 0
        serverless_flags: Set[str] = set()

        for backend_link in lb_entry["backendServiceLinks"]:
            detail = backend_details.get(backend_link)
            if not detail:
                continue
            backend_names.append(detail.get("name"))
            backend_count += len(detail.get("backends", []))
            for backend in detail.get("backends", []) or []:
                group = backend.get("group")
                if not group:
                    continue
                gdetail = neg_details.get(group)
                gmeta = parse_self_link(group)
                if gdetail:
                    netype = gdetail.get("networkEndpointType")
                    if netype == "SERVERLESS":
                        if gdetail.get("cloudRun"):
                            backend_types.add("ServerlessNEG(CloudRun)")
                            serverless_flags.add("CloudRun")
                        elif gdetail.get("cloudFunction"):
                            backend_types.add("ServerlessNEG(CloudFunctions)")
                            serverless_flags.add("CloudFunctions")
                        elif gdetail.get("appEngine"):
                            backend_types.add("ServerlessNEG(AppEngine)")
                            serverless_flags.add("AppEngine")
                        else:
                            backend_types.add("ServerlessNEG")
                            serverless_flags.add("ServerlessNEG")
                    elif netype == "PRIVATE_SERVICE_CONNECT":
                        backend_types.add("PSC")
                    elif netype == "INTERNET_IP_PORT":
                        backend_types.add("InternetNEG")
                    else:
                        backend_types.add(f"NEG({netype})" if netype else "NEG")
                elif gmeta.get("type") == "instanceGroups":
                    backend_types.add("InstanceGroup")
                else:
                    backend_types.add("UnknownBackend")
            for hc in detail.get("healthChecks", []) or []:
                lb_entry["healthChecks"].add(parse_self_link(hc).get("name") or hc)

        if lb_entry.get("primaryTarget") and "targetPools" in lb_entry["primaryTarget"]:
            if lb_entry["primaryTarget"] not in target_pool_details:
                detail, err = describe_generic(lb_entry["primaryTarget"])
                if err:
                    warnings.append(err)
                elif detail:
                    target_pool_details[lb_entry["primaryTarget"]] = detail
            pool_detail = target_pool_details.get(lb_entry["primaryTarget"])
            if pool_detail:
                backend_names.append(pool_detail.get("name"))
                backend_count += len(pool_detail.get("instances", []))
                backend_types.add("Instance")

        serverless = bool(serverless_flags)
        if serverless:
            serverless_lb_count += 1

        record = {
            "name": None,
            "projectId": lb_entry["projectId"],
            "type": lb_entry["type"],
            "scope": "global" if any(fr["scope"] == "global" for fr in lb_entry["forwardingRules"]) else "regional",
            "region": None,
            "scheme": lb_entry["scheme"],
            "ipAddresses": sorted(filter(None, lb_entry["ipAddresses"])),
            "ports": sorted(lb_entry["ports"]),
            "protocols": sorted(lb_entry["protocols"]),
            "forwardingRules": [f"{fr['region'] or 'global'}/{fr['name']}" for fr in lb_entry["forwardingRules"]],
            "targetProxy": None,
            "urlMap": None,
            "backendServices": sorted(filter(None, backend_names)),
            "backendTypes": sorted(filter(None, backend_types)),
            "backendCount": backend_count,
            "healthChecks": sorted(lb_entry["healthChecks"]),
            "network": sorted(lb_entry["networks"]),
            "subnetworks": sorted(lb_entry["subnetworks"]),
            "labels": lb_entry["labels"],
            "creationTimestamp": lb_entry["creationTimestamp"],
            "selfLink": None,
            "id": None,
            "protocolNotes": sorted(filter(None, lb_entry["protocolNotes"])),
            "serverlessBackends": sorted(serverless_flags),
        }

        regions = {fr["region"] for fr in lb_entry["forwardingRules"] if fr["region"]}
        if len(regions) == 1:
            record["region"] = regions.pop()
        elif len(regions) > 1:
            record["region"] = "multiple"

        target_link = lb_entry.get("primaryTarget")
        if target_link:
            detail = target_details.get(target_link)
            meta = parse_self_link(target_link)
            record["targetProxy"] = {
                "name": detail.get("name") if detail else meta.get("name"),
                "type": meta.get("type"),
                "scope": meta.get("scope"),
                "region": meta.get("region"),
            }
            if detail:
                record["name"] = detail.get("name")
                record["selfLink"] = detail.get("selfLink")
                record["id"] = detail.get("id")
                if detail.get("urlMap"):
                    urlmap_link = detail.get("urlMap")
                    record["urlMap"] = parse_self_link(urlmap_link).get("name")
            else:
                record["name"] = meta.get("name")
                record["selfLink"] = target_link
        else:
            record["name"] = lb_entry["forwardingRules"][0]["name"] if lb_entry["forwardingRules"] else "unknown"
            record["selfLink"] = key if key.startswith("https://") else None

        if lb_entry.get("pscDetails"):
            record["pscDetails"] = lb_entry["pscDetails"]

        lb_records.append(record)
        output["summary"]["countsByType"][record["type"]] += 1

    lb_records.sort(key=lambda r: (type_order.get(r["type"], 90), r["projectId"], r["name"] or ""))

    output["summary"]["total"] = len(lb_records)
    output["summary"]["serverlessCount"] = serverless_lb_count
    output["loadBalancers"] = lb_records
    output["warnings"] = sorted(set(warnings))

    json.dump(output, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
