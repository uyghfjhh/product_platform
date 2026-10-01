import { useMemo } from 'react';
import { createStudioBFFClient } from '@prisma/studio-core/data/bff';
import { createPostgresAdapter } from '@prisma/studio-core/data/postgres-core';
import { Studio } from '@prisma/studio-core/ui';
import '@prisma/studio-core/ui/index.css';

// Use Studio's public theme API. CSS variables follow the platform palette
// immediately, including overlays, without maintaining a second theme setting.
const palette = `
  --background: var(--bg-primary);
  --foreground: var(--text-primary);
  --card: var(--bg-surface);
  --card-foreground: var(--text-primary);
  --popover: var(--bg-surface-elevated);
  --popover-foreground: var(--text-primary);
  --primary: var(--border-active);
  --primary-foreground: var(--studio-on-accent);
  --secondary: var(--bg-surface);
  --secondary-foreground: var(--text-primary);
  --muted: var(--bg-surface-elevated);
  --muted-foreground: var(--text-muted);
  --accent: var(--app-selected);
  --accent-foreground: var(--app-selected-text);
  --destructive: var(--color-fail);
  --destructive-foreground: var(--studio-on-accent);
  --border: var(--border-subtle);
  --input: var(--border-medium);
  --ring: var(--border-active);
  --radius: 0.375rem;
  --chart-1: var(--border-active);
  --chart-2: var(--color-pass);
  --chart-background: var(--bg-primary);
  --chart-foreground: var(--text-primary);
  --chart-foreground-muted: var(--text-muted);
  --chart-grid: var(--border-subtle);
  --chart-tooltip-background: var(--bg-surface-elevated);
  --chart-tooltip-foreground: var(--text-primary);
  --chart-crosshair: var(--border-active);
  --studio-staged-cell-background: color-mix(in srgb, var(--border-active) 12%, var(--bg-primary));
`;
const theme = `:root { ${palette} } .dark { ${palette} }`;

export default function StudioPanel({ environmentId, nodeId }: { environmentId: string; nodeId?: string }) {
  const adapter = useMemo(() => {
    const target = nodeId ? `/nodes/${encodeURIComponent(nodeId)}` : '';
    const client = createStudioBFFClient({
      url: `/api/v1/environments/${encodeURIComponent(environmentId)}/studio${target}`,
    });
    return createPostgresAdapter({ executor: client });
  }, [environmentId, nodeId]);
  return <Studio adapter={adapter} theme={theme} />;
}
