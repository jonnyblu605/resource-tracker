import { tool } from "@opencode-ai/plugin";

declare const require: (name: string) => any;
declare const process: any;

const childProcess = require("child_process");
const pathModule = require("path");

export default tool({
  description: "Collect GCP load balancer inventory by invoking lb_inventory.py",
  args: {
    orgId: tool.schema.string().optional().describe("Override GCP organization ID"),
    projectFilter: tool.schema
      .string()
      .optional()
      .describe("Additional filter expression appended to gcloud projects list"),
    maxWorkers: tool.schema
      .number()
      .int()
      .positive()
      .optional()
      .describe("Maximum parallel workers for inventory collection"),
    python: tool.schema
      .string()
      .optional()
      .describe("Python interpreter to use (defaults to python3)"),
    scriptPath: tool.schema
      .string()
      .optional()
      .describe("Alternate path to lb_inventory.py (defaults to project root)"),
  },
  async execute(args) {
    const python = args.python ?? "python3";
    const cwd = typeof process?.cwd === "function" ? process.cwd() : ".";
    const scriptPath = pathModule.resolve(
      cwd,
      args.scriptPath ?? "lb_inventory.py",
    );

    const env = {
      ...process.env,
    };

    if (args.orgId) {
      env.GCP_ORG_ID = args.orgId;
    }
    if (args.projectFilter) {
      env.GCP_PROJECT_FILTER = args.projectFilter;
    }
    if (args.maxWorkers !== undefined) {
      env.GCP_LB_MAX_WORKERS = String(args.maxWorkers);
    }

    return new Promise<string>((resolvePromise, rejectPromise) => {
      const child: any = childProcess.spawn(python, [scriptPath], {
        env,
        cwd,
      });

      let stdout = "";
      let stderr = "";

      child.stdout?.setEncoding?.("utf8");
      child.stdout?.on?.("data", (chunk: any) => {
        stdout += String(chunk);
      });

      child.stderr?.setEncoding?.("utf8");
      child.stderr?.on?.("data", (chunk: any) => {
        stderr += String(chunk);
      });

      child.on?.("error", (error: unknown) => {
        let message: string;
        if (error && typeof error === "object" && "message" in error) {
          message = String((error as { message: unknown }).message);
        } else {
          message = String(error);
        }
        rejectPromise(
          new Error(
            `Failed to launch ${python} ${scriptPath}: ${message}`,
          ),
        );
      });

      child.on?.("close", (code: number | null) => {
        if (code === 0) {
          resolvePromise(stdout.trim());
        } else {
          rejectPromise(
            new Error(
              `lb_inventory.py exited with code ${code}. stderr: ${stderr.trim()}`,
            ),
          );
        }
      });
    });
  },
});