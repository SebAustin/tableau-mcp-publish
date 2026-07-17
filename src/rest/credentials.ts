/**
 * Embedded connection credentials for a Publish Datasource request — Phase E2 slice C.
 *
 * Tableau's Publish Datasource multipart `tsRequest` accepts an optional
 * `<connectionCredentials name="db-username" password="db-password" embed="true" oAuth="false" />`
 * element nested inside `<datasource>`, alongside `<project>`. When present, Tableau embeds
 * these credentials on the published datasource's connection so Cloud-hosted refreshes
 * (see `schedule_refresh` in `rest/schedules.ts`) can run without a live interactive sign-in.
 *
 * Only the common single-connection case is implemented here (one
 * `<connectionCredentials>` sibling of `<project>`); a multi-connection federated
 * datasource would need one per named connection, which is out of scope for this slice.
 *
 * SECURITY: `username`/`password` must come from environment/config — see the
 * `create_live_datasource` tool description — NEVER hardcoded or logged.
 * `xmlEscape` is applied to every attribute value before it touches outgoing XML.
 * `restClient.ts` only ever logs the *response* body of a failed call, never the
 * outgoing request body, so a failed publish can never leak `password` to stderr
 * (see `tests/secrets.test.ts` for the standing assertion).
 */

import { z } from "zod";
import { xmlEscape } from "./xml.js";

export const DatasourceCredentialsSchema = z.object({
  username: z
    .string()
    .min(1)
    .describe("Database username. Source this from your environment — never hardcode it."),
  password: z
    .string()
    .min(1)
    .describe(
      "Database password/secret. Source this from your environment — never hardcode or log it.",
    ),
  embed: z
    .boolean()
    .default(true)
    .describe("Whether Tableau embeds the credentials on the connection (almost always true)."),
  oauth: z
    .boolean()
    .default(false)
    .describe("Whether this is an OAuth credential rather than a username/password pair."),
});
export type DatasourceCredentials = z.infer<typeof DatasourceCredentialsSchema>;

/**
 * Builds the `<connectionCredentials>` child of `<datasource>` in the publish
 * `tsRequest`. Every attribute value is `xmlEscape`d. Never logs `password` —
 * callers must not `JSON.stringify`/log the return value either.
 */
export function buildConnectionCredentialsXml(creds: DatasourceCredentials): string {
  return (
    `<connectionCredentials name="${xmlEscape(creds.username)}" password="${xmlEscape(creds.password)}" ` +
    `embed="${creds.embed}" oAuth="${creds.oauth}" />`
  );
}
