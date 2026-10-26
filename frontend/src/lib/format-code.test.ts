import { describe, expect, it } from "vitest";
import { canFormat, formatCode } from "./format-code";

describe("formateo de código", () => {
  it("detecta los tipos soportados", () => {
    expect(canFormat("src/main.ts")).toBe(true);
    expect(canFormat("estilos.css")).toBe(true);
    expect(canFormat("programa.py")).toBe(false);
  });

  it("formatea JavaScript y JSON con Prettier", async () => {
    expect(await formatCode("a.js", "const x={a:1,b:[1,2]}")).toBe("const x = { a: 1, b: [1, 2] };\n");
    expect(await formatCode("a.json", '{"a":1}')).toBe('{ "a": 1 }\n');
  });

  it("informa de errores de sintaxis", async () => {
    await expect(formatCode("a.js", "const = ;")).rejects.toThrow();
  });
});
