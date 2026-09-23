import { lazy, Suspense } from 'react';
import { Spin } from 'antd';

const CodeEditor = lazy(() => import('./CodeEditor'));

type Props = {
  value: string;
  language: string;
  onChange?: (value: string) => void;
  readOnly?: boolean;
  height?: number;
};

export default function LazyCodeEditor(props: Props) {
  return <Suspense fallback={<div className="editor-loading" style={{ height: props.height || 360 }}><Spin /></div>}>
    <CodeEditor {...props} />
  </Suspense>;
}
