import type { ProductFrontend } from '../../frontend/src/products/types';

const frontend: ProductFrontend = {
  testMode: (environment) => environment?.deployment_target === 'streaming.mac' ? 'mac' : 'mmr',
  test: (requestedMode) => {
    const mode = requestedMode === 'mac' ? 'mac' : 'mmr';
    return {
      productId: 'fbase-database', mode, action: 'tests.fbase', suiteFilter: mode,
      supportsLegacyReports: false, supportsTerminal: false,
      clusterForSuite: (suite) => suite === 'mac' || suite === 'mmr' ? suite : mode,
    };
  },
};

export default frontend;
