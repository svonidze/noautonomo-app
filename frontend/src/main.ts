import { createApp } from 'vue';
import Shell from './shell/Shell.vue';
import { createProductionRuntime } from './shell/production-runtime.ts';
import { createPublicRuntime, readRuntimeConfig, publicLocale } from './shell/public-runtime.ts';
import { appRuntimeKey } from './shell/runtime.ts';
import { setLocale } from './core/i18n.ts';
import './styles/styles.css';

const config = readRuntimeConfig();
const runtime = config ? createPublicRuntime(config) : createProductionRuntime();
if (config) window.addEventListener('popstate', () => setLocale(publicLocale()));
createApp(Shell).provide(appRuntimeKey, runtime).use(runtime.router).mount(document.body);
