export type ExtensionPreset = {
  name: string;
  label: string;
  defaultPreload: boolean;
};

export const CORE_EXTENSIONS = new Set(['fdd_mmr', 'fbase_mac', 'citus']);

export const EXTENSION_PRESETS: ExtensionPreset[] = [
  { name: 'pg_deltax', label: 'DeltaX', defaultPreload: true },
  { name: 'timescaledb', label: 'TimescaleDB', defaultPreload: true },
];

const LABEL_BY_NAME: Record<string, string> = Object.fromEntries(
  EXTENSION_PRESETS.map((ext) => [ext.name, ext.label]),
);

export function registerProductExtensions(
  products: Array<{ extensions?: Array<{ id: string; title: string; preload?: boolean }> }>,
): void {
  for (const product of products) {
    for (const ext of product.extensions || []) {
      LABEL_BY_NAME[ext.id] = ext.title || ext.id;
      if (!EXTENSION_PRESETS.some((preset) => preset.name === ext.id)) {
        EXTENSION_PRESETS.push({
          name: ext.id,
          label: ext.title || ext.id,
          defaultPreload: ext.preload ?? true,
        });
      }
    }
  }
}

export function formatExtensionLabel(name: string, version?: string): string {
  const label = LABEL_BY_NAME[name] || name;
  return version ? `${label} ${version}` : label;
}

export function extensionCssClass(name: string): string {
  return `ext-${name.replace(/[^a-z0-9_-]/g, '_')}`;
}
