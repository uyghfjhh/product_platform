import { Alert, Form, Input, InputNumber } from 'antd';

export default function EnvironmentFields() {
  return <>
    <Alert type="info" showIcon message="登记现有金仓环境"
      description="填写主库连接；如有备库，一并填写。保存前会检查版本、实际数据目录、主备角色和复制关系。" />
    <div className="form-grid" style={{ marginTop: 16 }}>
      <Form.Item label="备库主机（可选，默认同主库）" name="standby_host"><Input placeholder="127.0.0.1" /></Form.Item>
      <Form.Item label="备库端口（留空登记单机）" name="standby_port"><InputNumber min={1} max={65535} style={{ width: '100%' }} /></Form.Item>
    </div>
  </>;
}
