import React from 'react';
import { Button, Result } from 'antd';

type Props = { children: React.ReactNode };
type State = { error: Error | null };

/** 全局错误边界：单页渲染异常（含 WebGL/3D）不再拖垮整个工作台。 */
export default class PlatformErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error('页面渲染异常', error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <Result
        status="warning"
        title="页面渲染异常"
        subTitle={this.state.error.message || '组件发生未知错误'}
        extra={(
          <Button type="primary" onClick={() => this.setState({ error: null })}>
            重试
          </Button>
        )}
      />
    );
  }
}
