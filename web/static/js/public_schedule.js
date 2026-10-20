/* Optional usability and WebMCP enhancement. All schedule text is server-rendered. */
(() => {
  'use strict';
  const filter = document.querySelector('[data-entity-filter]');
  if (filter) {
    const input = filter.querySelector('input');
    const items = Array.from(document.querySelectorAll('[data-entity-list] li'));
    const status = filter.querySelector('[data-filter-status]');
    filter.hidden = false;
    input.addEventListener('input', () => {
      const query = input.value.trim().toLocaleLowerCase('ru');
      let visible = 0;
      for (const item of items) {
        item.hidden = !item.textContent.toLocaleLowerCase('ru').includes(query);
        if (!item.hidden) visible += 1;
      }
      status.textContent = query ? `Найдено: ${visible}` : '';
    });
  }
  const dateForm = document.querySelector('[data-date-form]');
  if (dateForm) {
    dateForm.hidden = false;
    dateForm.addEventListener('submit', (event) => {
      event.preventDefault();
      const chosen = dateForm.querySelector('input').value;
      if (/^\d{4}-\d{2}-\d{2}$/.test(chosen)) window.location.assign(`${dateForm.dataset.entityUrl}/${chosen}`);
    });
  }
  // This draft browser capability is progressive enhancement only. Do not
  // polyfill, send the conversation, or require it for public page access.
  const context = document.modelContext;
  if (!context || typeof context.registerTool !== 'function') return;
  const entityProperties = {
    entity_type: {type: 'string', enum: ['group', 'teacher', 'audience']},
    entity: {type: 'string', minLength: 1, maxLength: 200}
  };
  async function read(path, values = {}) {
    const url = new URL(`/api/v2/${path}`, window.location.origin);
    for (const [key, value] of Object.entries(values)) {
      if (value !== undefined && value !== null) url.searchParams.set(key, String(value));
    }
    const response = await fetch(url, {method: 'GET', credentials: 'omit', redirect: 'error', headers: {Accept: 'application/json'}});
    const result = await response.json();
    return response.ok ? result : {error: result.error || 'Schedule service unavailable', status: response.status};
  }
  const tools = [
    {
      name: 'get_kkep_catalog',
      description: 'Read public KKEP college directories and archived schedule dates. College is in Krasnodar, timezone Europe/Moscow. No personal or homework data.',
      inputSchema: {type: 'object', properties: {}, additionalProperties: false},
      execute: async () => {
        const {group_dates: _groupDates, ...catalog} = await read('catalog');
        return catalog;
      }
    },
    {
      name: 'resolve_kkep_entity',
      description: 'Find exact KKEP group, teacher or classroom names. If multiple matches exist, ask which one; never guess.',
      inputSchema: {type: 'object', properties: {entity_type: entityProperties.entity_type, query: {type: 'string', minLength: 1, maxLength: 200}}, required: ['entity_type', 'query'], additionalProperties: false},
      execute: (values) => read('resolve', values)
    },
    {
      name: 'get_kkep_schedule',
      description: 'Read published KKEP schedule for an exact group, teacher or classroom on a date or up to 31 days. Dates ISO YYYY-MM-DD, relative day in Europe/Moscow. missing is no data, not no classes. Cite source.url.',
      inputSchema: {type: 'object', properties: {...entityProperties, date: {type: 'string'}, start: {type: 'string'}, end: {type: 'string'}}, required: ['entity_type', 'entity'], additionalProperties: false},
      execute: (values) => read('schedule', values)
    }
  ];
  for (const tool of tools) {
    try {
      Promise.resolve(context.registerTool({...tool, annotations: {readOnlyHint: true}})).catch(() => {});
    } catch (_) {
      // Unsupported draft variants must not affect ordinary navigation.
    }
  }
})();
