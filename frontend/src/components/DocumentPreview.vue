<script setup lang="ts">
import { documentHref, type ViewServices } from '../vue/services.ts';
defineProps<{ services?: ViewServices; documentId: string; title: string }>();
</script>
<template>
  <iframe :title="title" :src="documentHref(services, documentId, true)"></iframe>
</template>
