import { createWebHistory } from 'vue-router';
import { createAppRouter } from './create-router.ts';
export const createProductionRouter = () => createAppRouter(createWebHistory());
