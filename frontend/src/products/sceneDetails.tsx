import { productFrontends } from './generated';

type Props = { kind: string; details: Record<string, unknown> };

export default function ProductSceneDetails({ kind, details }: Props) {
  for (const extension of Object.values(productFrontends)) {
    const scene = extension.sceneDetails;
    if (scene && kind.startsWith(scene.kindPrefix)) {
      const Details = scene.component;
      return <Details details={details} />;
    }
  }
  return null;
}
