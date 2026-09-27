import type { RegressionTopology } from './topologyModel';

export function mountReferenceThreeScene(
  container: HTMLElement,
  topology: RegressionTopology,
  onSelect: (name: string) => void,
): () => void;
