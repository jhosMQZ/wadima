import type { OrgFull } from "../api/types";

export interface IdentityValue {
  platform_name: string;
  legal_name: string;
  short_mark: string;
  description: string;
  primary_color: string;
  secondary_color: string;
  timezone: string;
}

export function identityOf(org: OrgFull): IdentityValue {
  return {
    platform_name: org.platform_name,
    legal_name: org.legal_name,
    short_mark: org.short_mark,
    description: org.description,
    primary_color: org.primary_color,
    secondary_color: org.secondary_color,
    timezone: org.timezone,
  };
}
