import type { Product } from '../platform/api';
import { productFrontends } from './generated';
import type { ProductFrontend } from './types';

export type DeploymentProductAdapter = {
  productId: string;
  hasProfileWizard: boolean;
  profilePath?: (environmentId: string) => string;
  workspaceClass?: string;
};

export function deploymentAdapter(product: Product | undefined): DeploymentProductAdapter {
  return product && productFrontends[product.id]?.deployment?.() || {
    productId: product?.id || '', hasProfileWizard: false,
  };
}

export function deploymentFrontend(product: Product | undefined): ProductFrontend | undefined {
  return product && productFrontends[product.id] || undefined;
}
