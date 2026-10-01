import type { ProductFrontend } from '../../frontend/src/products/types';

const frontend: ProductFrontend = {
  test: () => ({
    productId: 'demo', mode: 'smoke', action: 'tests.demo', suiteFilter: 'smoke',
    supportsLegacyReports: false,
    clusterForSuite: () => undefined,
  }),
};

export default frontend;
