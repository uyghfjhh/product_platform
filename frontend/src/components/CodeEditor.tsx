import Editor, { loader } from '@monaco-editor/react';
import * as monaco from 'monaco-editor';

// 使用随前端打包的 Monaco，浏览器无需访问外部 CDN。
loader.config({ monaco });

type Props = {
  value: string;
  language: string;
  onChange?: (value: string) => void;
  readOnly?: boolean;
  height?: number;
};

export default function CodeEditor({ value, language, onChange, readOnly = false, height = 360 }: Props) {
  return (
    <div className="code-editor" style={{ height }}>
      <Editor
        height="100%"
        language={language}
        value={value}
        onChange={(text) => onChange?.(text || '')}
        options={{
          minimap: { enabled: false },
          readOnly,
          fontSize: 13,
          lineNumbers: 'on',
          scrollBeyondLastLine: false,
          automaticLayout: true,
          wordWrap: 'on',
        }}
      />
    </div>
  );
}
