import type { ProductFrontend } from '../../frontend/src/products/types';
import { post, type Environment } from '../../frontend/src/platform/api';
import EnvironmentFields from './frontend/EnvironmentFields';

const frontend: ProductFrontend = {
  environmentRegistration: {
    defaults: { port: 54321, database_name: 'kingbase', database_user: 'kingbase', standby_host: '', standby_port: 54322 },
    Fields: EnvironmentFields,
    submit: (values) => post<Environment>('/products/kingbase/environments', {
      id: values.id, title: values.title, host: values.host, port: values.port,
      database_name: values.database_name, database_user: values.database_user,
      standby_host: values.standby_host || '', standby_port: values.standby_port || null,
    }),
  },
};
export default frontend;
