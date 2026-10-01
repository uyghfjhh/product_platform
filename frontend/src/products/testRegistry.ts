import type { Environment, Product } from '../platform/api';
import { productFrontends } from './generated';
import type { ProductFrontend } from './types';

export type TestMode = string;

export type TestProductAdapter = {
  productId: string;
  mode: TestMode;
  action: string;
  suiteFilter?: string;
  supportsLegacyReports: boolean;
  sourceStatusPath?: (environmentId?: string) => string;
  artifactPath?: (target: string, environmentId?: string) => string;
  reportPath?: (environmentId: string, format: 'junit' | 'html') => string;
  clusterForSuite: (suite: string) => string | undefined;
};

export function testAdapter(product: Product | undefined, mode?: TestMode): TestProductAdapter {
  const extension = product && productFrontends[product.id]?.test?.(mode);
  return extension || {
    productId: product?.id || '', mode: mode || '', action: '',
    supportsLegacyReports: false,
    clusterForSuite: () => undefined,
  };
}

export function testMode(product: Product | undefined, environment?: Environment): TestMode | undefined {
  return product && productFrontends[product.id]?.testMode?.(environment) || undefined;
}

export function testFrontend(product: Product | undefined): ProductFrontend | undefined {
  return product && productFrontends[product.id] || undefined;
}
