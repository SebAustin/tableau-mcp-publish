import { describe, it, expect } from "vitest";
import {
  buildConnectionCredentialsXml,
  DatasourceCredentialsSchema,
} from "../src/rest/credentials.js";

describe("DatasourceCredentialsSchema", () => {
  it("defaults embed=true and oauth=false", () => {
    const parsed = DatasourceCredentialsSchema.parse({ username: "u", password: "p" });
    expect(parsed).toEqual({ username: "u", password: "p", embed: true, oauth: false });
  });

  it("rejects an empty username or password", () => {
    expect(() => DatasourceCredentialsSchema.parse({ username: "", password: "p" })).toThrow();
    expect(() => DatasourceCredentialsSchema.parse({ username: "u", password: "" })).toThrow();
  });
});

describe("buildConnectionCredentialsXml", () => {
  it("emits name/password/embed/oAuth attributes", () => {
    const xml = buildConnectionCredentialsXml({
      username: "svc_user",
      password: "s3cr3t",
      embed: true,
      oauth: false,
    });
    expect(xml).toBe(
      '<connectionCredentials name="svc_user" password="s3cr3t" embed="true" oAuth="false" />',
    );
  });

  it("xml-escapes special characters in both username and password", () => {
    const xml = buildConnectionCredentialsXml({
      username: `u&<>"'`,
      password: `p&<>"'`,
      embed: false,
      oauth: true,
    });
    expect(xml).toContain('name="u&amp;&lt;&gt;&quot;&apos;"');
    expect(xml).toContain('password="p&amp;&lt;&gt;&quot;&apos;"');
    expect(xml).toContain('embed="false"');
    expect(xml).toContain('oAuth="true"');
  });
});
