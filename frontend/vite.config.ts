import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  resolve: { dedupe: ['react', 'react-dom', 'antd', '@ant-design/icons', 'gsap', 'dompurify', 'three'] },
  build: { cssMinify: false },
  server: { proxy: { '/api': process.env.PRODUCT_PLATFORM_API_ORIGIN || 'http://127.0.0.1:8080' } },
});
