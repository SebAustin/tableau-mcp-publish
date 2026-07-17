/**
 * Cloud extract-refresh scheduling tools — Phase E2 slice B.
 *
 * Tableau Cloud has no classic Server-style shared schedules over REST; each
 * task carries its own embedded frequency + interval schedule (see
 * `src/rest/schedules.ts` for the full API + VERIFY-LIVE documentation).
 * These tools are distinct from `refresh_datasource` (`src/tools/content.ts`),
 * which triggers a single immediate refresh with no recurring schedule.
 */

import { z } from "zod";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { type ToolContext, toolResult } from "./context.js";
import {
  ExtractRefreshTypeSchema,
  FrequencyDetailsSchema,
  FrequencySchema,
  type ScheduleTarget,
} from "../rest/schedules.js";

const targetTypeSchema = z.enum(["datasource", "workbook"]);

/**
 * Always surfaced (see the connectivity-nuance section of `rest/schedules.ts`):
 * Cloud can only *execute* a scheduled refresh for a cloud-reachable
 * connection with embedded credentials. File-based extracts publish and
 * schedule successfully, but Cloud cannot itself refresh them without
 * Tableau Bridge — creation may succeed while every run fails. We can't
 * reliably detect this case from the API response, so this note is always
 * included rather than only surfaced conditionally.
 */
const CLOUD_REFRESH_CONNECTIVITY_NOTE =
  "Tableau Cloud only executes this schedule for connections that are cloud-reachable with " +
  "embedded credentials (e.g. Snowflake, a direct cloud database). A datasource published from " +
  "a local file (CSV/Excel/local Hyper extract) accepts this schedule successfully, but Cloud " +
  "cannot refresh a file-based extract without Tableau Bridge — the task may be created while " +
  "its runs fail. This note is always included since it can't be reliably detected from the API " +
  "response.";

export function registerScheduleTools(server: McpServer, ctx: ToolContext): void {
  server.registerTool(
    "schedule_refresh",
    {
      title: "Schedule a Cloud extract refresh",
      description:
        "Create a recurring extract-refresh task on Tableau Cloud for a published datasource or " +
        "workbook, with an embedded schedule (frequency + start/end time + intervals — Cloud has " +
        "no shared Server-style schedules, unlike Tableau Server). Distinct from refresh_datasource, " +
        "which triggers a single immediate refresh instead of a recurring one. `type` defaults to " +
        'FullRefresh; "IncrementalRefresh" is supported but its exact token spelling is unverified ' +
        "against a live site (VERIFY-LIVE) — prefer FullRefresh until confirmed. " +
        "IMPORTANT: Cloud can only run this schedule for a cloud-reachable connection with embedded " +
        "credentials; file-based extracts accept the schedule but Cloud cannot refresh them without " +
        "Tableau Bridge (always see the returned `note`). " +
        "Returns { taskId, frequency, nextRunAt?, note }.",
      inputSchema: {
        targetType: targetTypeSchema.describe("Whether targetId is a datasource or a workbook."),
        targetId: z.string().min(1).describe("LUID of the published datasource or workbook."),
        type: ExtractRefreshTypeSchema.default("FullRefresh").describe(
          "FullRefresh (default) or IncrementalRefresh (token unverified — see description).",
        ),
        frequency: FrequencySchema.describe("Hourly, Daily, Weekly, or Monthly."),
        frequencyDetails: FrequencyDetailsSchema.describe(
          "start (required, HH:MM:SS) + optional end + intervals. Hourly needs >=1 interval with " +
            "hours; Weekly needs >=1 interval with weekDay; Monthly needs exactly one interval with " +
            "monthDay; Daily needs no intervals.",
        ),
      },
      outputSchema: {
        taskId: z.string(),
        frequency: z.string().optional(),
        nextRunAt: z.string().optional(),
        note: z.string(),
      },
    },
    async ({ targetType, targetId, type, frequency, frequencyDetails }) => {
      const target: ScheduleTarget = { kind: targetType, id: targetId };
      const task = await ctx.rest.scheduleRefresh(target, type, { frequency, frequencyDetails });
      return toolResult(
        `Created ${frequency} refresh schedule (task ${task.taskId}) for ${targetType} ${targetId}.`,
        {
          taskId: task.taskId,
          ...(task.frequency !== undefined ? { frequency: task.frequency } : {}),
          ...(task.nextRunAt !== undefined ? { nextRunAt: task.nextRunAt } : {}),
          note: CLOUD_REFRESH_CONNECTIVITY_NOTE,
        },
      );
    },
  );

  server.registerTool(
    "list_refresh_schedules",
    {
      title: "List Cloud extract-refresh schedules",
      description:
        "List every extract-refresh task (recurring schedule) on the site, with its embedded " +
        "frequency and next-run time when the API reports one. Returns { schedules: [...] }.",
      inputSchema: {},
      outputSchema: {
        schedules: z.array(
          z.object({
            taskId: z.string(),
            type: z.string(),
            targetKind: z.enum(["datasource", "workbook"]).optional(),
            targetId: z.string().optional(),
            frequency: z.string().optional(),
            nextRunAt: z.string().optional(),
          }),
        ),
      },
    },
    async () => {
      const tasks = await ctx.rest.listRefreshSchedules();
      const schedules = tasks.map((t) => ({
        taskId: t.taskId,
        type: t.type,
        ...(t.targetKind !== undefined ? { targetKind: t.targetKind } : {}),
        ...(t.targetId !== undefined ? { targetId: t.targetId } : {}),
        ...(t.frequency !== undefined ? { frequency: t.frequency } : {}),
        ...(t.nextRunAt !== undefined ? { nextRunAt: t.nextRunAt } : {}),
      }));
      return toolResult(`Found ${schedules.length} refresh schedule(s).`, { schedules });
    },
  );

  server.registerTool(
    "delete_refresh_schedule",
    {
      title: "Delete a Cloud extract-refresh schedule",
      description:
        "Delete a recurring extract-refresh task by its taskId. DESTRUCTIVE and irreversible — " +
        "requires confirm=true. Returns { deleted, taskId }.",
      inputSchema: {
        taskId: z.string().min(1).describe("taskId from schedule_refresh or list_refresh_schedules."),
        confirm: z
          .boolean()
          .default(false)
          .describe("Must be explicitly true; deletion is refused otherwise."),
      },
      outputSchema: { deleted: z.boolean(), taskId: z.string() },
    },
    async ({ taskId, confirm }) => {
      if (!confirm) {
        throw new Error(
          `Refusing to delete refresh schedule ${taskId}: pass confirm=true to authorize this irreversible action.`,
        );
      }
      await ctx.rest.deleteRefreshSchedule(taskId);
      return toolResult(`Deleted refresh schedule ${taskId}.`, { deleted: true, taskId });
    },
  );
}
