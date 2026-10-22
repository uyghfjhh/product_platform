import { createElement, lazy, Suspense, type ComponentType } from 'react';
import type { ProductFrontend } from '../../frontend/src/products/types';

function lazyComponent<P extends object>(loader: () => Promise<{ default: ComponentType<P> }>): ComponentType<P> {
  const Component = lazy(loader);
  return function LazyProductComponent(props: P) {
    return createElement(Suspense, { fallback: null }, createElement(Component, props));
  };
}

const ReportViewer = lazyComponent(() => import('./frontend/ReportViewer'));
const SceneDetails = lazyComponent(() => import('./frontend/SceneDetails'));
const TestSettings = lazyComponent(() => import('./frontend/TestSettings'));
const TestPreparation = lazyComponent(() => import('./frontend/TestPreparation'));

const frontend: ProductFrontend = {
  TestSettings,
  TestPreparation,
  ReportViewer,
  sceneDetails: { kindPrefix: 'fbasecman.', component: SceneDetails },
  testMode: () => 'cman',
  deployment: () => ({
    productId: 'fbasecman',
    hasProfileWizard: true,
    profilePath: (environmentId) => `/environments/${encodeURIComponent(environmentId)}/fbasecman-profile`,
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
