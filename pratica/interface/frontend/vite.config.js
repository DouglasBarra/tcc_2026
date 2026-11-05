import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Dev: o frontend sobe na 5173 e repassa /api para o backend na 8000 (nenhum CORS necessario).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    host: '127.0.0.1',
    proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false } },
  },
  build: { outDir: 'dist', sourcemap: false },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/__testes__/setup.js'],
    include: ['src/**/*.test.{js,jsx}'],
    css: false,
  },
});
