<script setup>
/**
 * Ветка оргсхемы (рекурсивно): карточка узла через слот и, если ветка раскрыта, дочерние узлы.
 * Дочерние без своих дочерних — столбиком под родителем. Линии рисует CSS в OrgChart.vue.
 */
defineOptions({ name: 'OrgChartBranch' })
defineProps({
  node: { type: Object, required: true },
  expanded: { type: Object, required: true },
  path: { type: Object, required: true },
})
const isStack = (node) => node.children.length > 1 && node.children.every((child) => !child.children.length)
// Ствол к детям подсвечен, только если путь идёт дальше вниз (а не заканчивается на этом узле)
const pathGoesDown = (node, path) => path.has(node.id) && node.children.some((child) => path.has(child.id))
</script>

<template>
  <li class="oc-node" :class="{ 'oc-node--path': path.has(node.id) }">
    <div class="oc-card-slot" :data-node-id="node.id">
      <slot :node="node" :expanded="expanded.has(node.id)" :on-path="path.has(node.id)" />
    </div>
    <ul v-if="node.children.length && expanded.has(node.id)" class="oc-level" :class="{ 'oc-level--path': pathGoesDown(node, path), 'oc-level--stack': isStack(node) }">
      <OrgChartBranch v-for="child in node.children" :key="child.id" :node="child" :expanded="expanded" :path="path">
        <template #default="slotProps"><slot v-bind="slotProps" /></template>
      </OrgChartBranch>
    </ul>
  </li>
</template>
