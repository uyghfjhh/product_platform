import type { ProductFrontend } from '../../frontend/src/products/types';
import ReportViewer from './frontend/ReportViewer';
import SceneDetails from './frontend/SceneDetails';
import TestSettings from './frontend/TestSettings';

const frontend: ProductFrontend = {
  TestSettings,
  ReportViewer,
  sceneDetails: { kindPrefix: 'fbasecman.', component: SceneDetails },
  testMode: () => 'cman',
  deployment: () => ({
    productId: 'fbasecman',
    hasProfileWizard: true,
    profilePath: (environmentId) => `/environments/${encodeURIComponent(environmentId)}/fbasecman-profile`,
    fixtureAction: { id: 'tests.prepare_fbasecman', title: '准备 fbasecman 测试夹具', capability: 'tests', changes_environment: true },
    workspaceClass: 'cman-deploy-workspace',
  }),
  test: () => ({
    productId: 'fbasecman', mode: 'cman', action: 'tests.fbasecman',
    supportsLegacyReports: true,
    sourceStatusPath: (environmentId) => `/fbasecman/case-statuses${environmentId ? `?environment_id=${encodeURIComponent(environmentId)}` : ''}`,
    artifactPath: (target, environmentId) => `/fbasecman/cases/${encodeURIComponent(target)}/artifacts${environmentId ? `?environment_id=${encodeURIComponent(environmentId)}` : ''}`,
    reportPath: (environmentId, format) => `/fbasecman/environments/${encodeURIComponent(environmentId)}/reports/${format}`,
    clusterForSuite: () => undefined,
  }),
};

export default frontend;
