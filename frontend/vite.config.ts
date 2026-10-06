import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({ plugins: [react()], build: {
  outDir: '../dist', emptyOutDir: true,
  rollupOptions: {output: {manualChunks: {react: ['react', 'react-dom', 'react-dom/client', 'react/jsx-runtime', 'scheduler']}}},
} });
