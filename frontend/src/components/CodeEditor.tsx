import Editor, { loader } from '@monaco-editor/react';
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api';
import 'monaco-editor/esm/vs/editor/edcore.main';
import 'monaco-editor/esm/vs/basic-languages/sql/sql.contribution';
import 'monaco-editor/esm/vs/basic-languages/pgsql/pgsql.contribution';
import 'monaco-editor/esm/vs/basic-languages/yaml/yaml.contribution';
import 'monaco-editor/esm/vs/basic-languages/ini/ini.contribution';
import 'monaco-editor/esm/vs/basic-languages/shell/shell.contribution';
import 'monaco-editor/esm/vs/language/json/monaco.contribution';

// 仅打包平台实际使用的语言高亮与编辑器核心，无需访问外部 CDN。
loader.config({ monaco });

type Props = {
  value: string;
  language: string;
  onChange?: (value: string) => void;
  readOnly?: boolean;
  height?: number;
  theme?: string;
};

export default function CodeEditor({ value, language, onChange, readOnly = false, height = 360, theme }: Props) {
  return (
    <div className="code-editor" style={{ height }}>
      <Editor
        height="100%"
        language={language}
        theme={theme}
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
