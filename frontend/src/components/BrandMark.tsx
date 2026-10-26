import { useBrand } from "../store/brand";

/** Logotipo de la organización o, si no tiene, sus iniciales sobre el color de marca. */
export function BrandLogo({ size = 28 }: { size?: number }) {
  const org = useBrand((s) => s.org);
  if (org.logo_url) {
    return <img className="brand-logo" src={org.logo_url} alt="" width={size} height={size} />;
  }
  return (
    <span
      className="brand-initials"
      aria-hidden="true"
      style={{
        width: size,
        height: size,
        fontSize: Math.round(size * (org.short_mark.length > 2 ? 0.36 : 0.46)),
      }}
    >
      {org.short_mark}
    </span>
  );
}

export function BrandMark({ showLegal = false }: { showLegal?: boolean }) {
  const org = useBrand((s) => s.org);
  return (
    <span className="brand" data-testid="brand">
      <BrandLogo />
      <span className="brand-text">
        <span className="brand-name">{org.platform_name}</span>
        {showLegal && org.legal_name !== org.platform_name && (
          <span className="brand-legal">{org.legal_name}</span>
        )}
      </span>
    </span>
  );
}
