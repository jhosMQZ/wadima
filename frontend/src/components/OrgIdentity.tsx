import { useMemo, useRef, useState } from "react";
import { api, del } from "../api/client";
import type { OrgFull } from "../api/types";
import type { IdentityValue } from "../lib/identity";
import { contrast, ensureContrast, isHexColor } from "../lib/color";
import { ErrorBox } from "./ui";

const BG = "#12152b";

function ColorField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  const [text, setText] = useState(value);
  return (
    <label className="field">
      {label}
      <span className="row">
        <input
          type="color"
          value={isHexColor(value) ? value : "#000000"}
          onChange={(e) => {
            setText(e.target.value);
            onChange(e.target.value);
          }}
          aria-label={`${label} (selector)`}
          style={{ width: 48, padding: 2 }}
        />
        <input
          value={text}
          maxLength={7}
          pattern="^#[0-9a-fA-F]{6}$"
          onChange={(e) => {
            setText(e.target.value);
            if (isHexColor(e.target.value)) onChange(e.target.value.toLowerCase());
          }}
          aria-label={`${label} (hexadecimal)`}
          style={{ width: 110 }}
        />
      </span>
    </label>
  );
}

export function IdentityFields({
  value,
  onChange,
  disabled,
}: {
  value: IdentityValue;
  onChange: (v: IdentityValue) => void;
  disabled?: boolean;
}) {
  const set = (k: keyof IdentityValue) => (v: string) => onChange({ ...value, [k]: v });
  return (
    <fieldset className="stack" disabled={disabled} style={{ border: 0, padding: 0, margin: 0 }}>
      <div className="grid cols-2">
        <label className="field">
          Nombre visible de la plataforma
          <input
            value={value.platform_name}
            required
            minLength={2}
            maxLength={60}
            onChange={(e) => set("platform_name")(e.target.value)}
          />
        </label>
        <label className="field">
          Nombre de la organización (razón social)
          <input
            value={value.legal_name}
            required
            minLength={2}
            maxLength={160}
            onChange={(e) => set("legal_name")(e.target.value)}
          />
        </label>
        <label className="field">
          Iniciales o símbolo (1–4 caracteres)
          <input
            value={value.short_mark}
            required
            maxLength={4}
            onChange={(e) => set("short_mark")(e.target.value)}
          />
        </label>
        <TimezoneField value={value.timezone} onChange={set("timezone")} />
      </div>
      <label className="field">
        Descripción de la organización
        <textarea
          value={value.description}
          rows={2}
          maxLength={2000}
          onChange={(e) => set("description")(e.target.value)}
        />
      </label>
      <div className="grid cols-2">
        <ColorField label="Color principal" value={value.primary_color} onChange={set("primary_color")} />
        <ColorField
          label="Color secundario"
          value={value.secondary_color}
          onChange={set("secondary_color")}
        />
      </div>
      <BrandPreview value={value} />
    </fieldset>
  );
}

export function TimezoneField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const zones = useMemo(() => {
    try {
      return Intl.supportedValuesOf("timeZone");
    } catch {
      return ["America/Bogota", "UTC"];
    }
  }, []);
  return (
    <label className="field">
      Zona horaria de la organización
      <input
        list="tz-list"
        value={value}
        required
        onChange={(e) => onChange(e.target.value)}
        placeholder="America/Bogota"
      />
      <datalist id="tz-list">
        {zones.map((z) => (
          <option key={z} value={z} />
        ))}
      </datalist>
      <span className="small muted">Los registros se guardan en UTC y se muestran en esta zona.</span>
    </label>
  );
}

/** Vista previa con avisos de contraste: la interfaz ajusta el tono para que el texto siga siendo legible. */
export function BrandPreview({ value }: { value: IdentityValue }) {
  const primary = isHexColor(value.primary_color) ? value.primary_color : "#6fd3ff";
  const secondary = isHexColor(value.secondary_color) ? value.secondary_color : "#ffd166";
  const ratio = contrast(primary, BG);
  return (
    <div className="brand-preview" aria-label="Vista previa de la marca">
      <div className="brand-preview-bar">
        <span
          className="brand-initials"
          style={{
            background: primary,
            color: contrast(primary, "#000") > contrast(primary, "#fff") ? "#000" : "#fff",
          }}
        >
          {value.short_mark || "?"}
        </span>
        <strong style={{ color: ensureContrast(primary, BG) }}>{value.platform_name || "Nombre"}</strong>
        <span className="small" style={{ color: ensureContrast(secondary, BG, 3) }}>
          {value.legal_name}
        </span>
      </div>
      {ratio < 4.5 && (
        <p className="small muted" style={{ margin: "6px 0 0" }}>
          El color principal tiene poco contraste con el fondo ({ratio.toFixed(1)}:1); en textos y enlaces se
          usará una variante más clara para que sigan siendo legibles.
        </p>
      )}
    </div>
  );
}

export function LogoUploader({ org, onUpdated }: { org: OrgFull; onUpdated: (org: OrgFull) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="stack">
      <div className="row">
        {org.logo_url ? (
          <img src={org.logo_url} alt="Logotipo actual" className="logo-preview" />
        ) : (
          <span className="muted small">Sin logotipo: se muestran las iniciales.</span>
        )}
      </div>
      <div className="row wrap">
        <input
          ref={input}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          aria-label="Archivo de logotipo"
          onChange={async (e) => {
            const file = e.target.files?.[0];
            if (!file) return;
            setBusy(true);
            setError(null);
            try {
              const form = new FormData();
              form.append("file", file);
              onUpdated(await api<OrgFull>("/api/org/logo", { method: "PUT", form }));
            } catch (err) {
              setError((err as Error).message);
            } finally {
              setBusy(false);
              if (input.current) input.current.value = "";
            }
          }}
          disabled={busy}
        />
        {org.logo_url && (
          <button
            type="button"
            className="small ghost"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                onUpdated(await del<OrgFull>("/api/org/logo"));
              } catch (err) {
                setError((err as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            Quitar logotipo
          </button>
        )}
      </div>
      <span className="small muted">PNG, JPEG o WebP de hasta 512 KB (SVG no se admite por seguridad).</span>
      <ErrorBox error={error} />
    </div>
  );
}
