export type Duration = { years: number; months: number; days: number };
export type ProductDefaults = {
  default_version: string; selected: boolean; validity: Duration | null;
};
export type LicenseDefaults = {
  schema_version: 1; vendor: string; license_version: string | null; start_at: 'today';
  validity: Duration; purpose: string; save_to_directory: boolean; output_directory: string; mac_addrs: string[]; products: Record<string, ProductDefaults>;
};
export type LicenseOptions = {
  vendor: string; products: { name: string; version: string }[]; key_versions: string[];
  usable_key_versions: string[]; defaults: LicenseDefaults;
};

export function localDate(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

export function expirationDate(duration: Duration, start = new Date()) {
  const monthStart = new Date(start.getFullYear() + duration.years, start.getMonth() + duration.months, 1);
  const end = new Date(monthStart.getFullYear(), monthStart.getMonth(),
    Math.min(start.getDate(), new Date(monthStart.getFullYear(), monthStart.getMonth() + 1, 0).getDate()));
  end.setDate(end.getDate() + duration.days);
  return localDate(end);
}
