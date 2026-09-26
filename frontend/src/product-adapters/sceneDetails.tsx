import FbasecmanSceneDetails from './fbasecman/SceneDetails';

type Props = { kind: string; details: Record<string, unknown> };

export default function ProductSceneDetails({ kind, details }: Props) {
  if (kind.startsWith('fbasecman.')) {
    return <FbasecmanSceneDetails details={details} />;
  }
  return null;
}
