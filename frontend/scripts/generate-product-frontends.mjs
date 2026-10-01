import { readdir, access, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';

const root = resolve(import.meta.dirname, '../..');
const products = join(root, 'products');
const output = join(root, 'frontend/src/products/generated.ts');
const entries = [];

for (const item of await readdir(products, { withFileTypes: true })) {
  if (!item.isDirectory() || !/^[a-z][a-z0-9_-]{1,63}$/.test(item.name)) continue;
  try {
    await access(join(products, item.name, 'product.yaml'));
    await access(join(products, item.name, 'frontend.ts'));
    entries.push(item.name);
  } catch {
    // Only installed products with a frontend entry are registered.
  }
}

entries.sort();
const imports = entries.map((id, index) => `import product${index} from '../../../products/${id}/frontend';`).join('\n');
const rows = entries.map((id, index) => `  ${JSON.stringify(id)}: product${index},`).join('\n');
await writeFile(output, `// Generated from installed product packages. Do not edit.\nimport type { ProductFrontend } from './types';\n${imports}\n\nexport const productFrontends: Record<string, ProductFrontend> = {\n${rows}\n};\n`);
