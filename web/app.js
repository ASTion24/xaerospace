"use strict";

const LOCALE_STORAGE_KEY = "wms-aerospace-locale";
const ASSISTANT_SESSION_STORAGE_KEY = "wms-aerospace-assistant-session";
const WORKFLOW_STORAGE_KEY = "xaerospace-active-workflow";
const SUPPORTED_LOCALES = new Set(["zh-CN", "en"]);
const translations = globalThis.WMS_I18N;
let parameterGuide = null;

const state = {
  locale: loadInitialLocale(),
  serviceState: "connecting",
  assistantStatus: null,
  assistantSession: null,
  assistantDraft: null,
  assistantError: null,
  assistantBusy: false,
  taskFamilies: [],
  scenarios: [],
  capabilities: [],
  sourceView: "assistant",
  libraryMode: "families",
  workbenchView: "editor",
  activeBackend: "all",
  searchText: "",
  selectedFamilyId: null,
  selectedFamilyDetail: null,
  selectedVariantId: null,
  selectedScenarioId: null,
  selectedTaskId: null,
  editorMode: "new",
  editorView: "form",
  showAdvancedParameters: false,
  tasks: [],
  workflow: null,
  workflowHistory: [],
  workflowHistoryOpen: false,
  selectedResultTaskId: null,
  polling: false,
};

const elements = {};

const statusTranslationKeys = {
  queued: "statusQueued",
  running: "statusRunning",
  completed: "statusCompleted",
  failed: "statusFailed",
  interrupted: "statusInterrupted",
  skipped: "statusSkipped",
  idle: "statusIdle",
};

document.addEventListener("DOMContentLoaded", () => {
  bindElements();
  bindEvents();
  applyLocale();
  initialize();
});

function bindElements() {
  const ids = [
    "serviceDot",
    "serviceStatus",
    "languageSwitcher",
    "backendCount",
    "protocolVersion",
    "sourceViewTabs",
    "assistantSourcePane",
    "catalogSourcePane",
    "libraryTitle",
    "libraryCount",
    "assistantStatus",
    "assistantConversation",
    "assistantPromptCaption",
    "assistantPrompt",
    "generateAssistantDraft",
    "newAssistantSession",
    "assistantResult",
    "applyAssistantDraft",
    "confirmAndRunAssistantDraft",
    "assistantExecutionNote",
    "libraryTabs",
    "librarySearchLabel",
    "scenarioSearch",
    "backendFilters",
    "libraryList",
    "workflowName",
    "toggleWorkflowHistory",
    "workflowHistoryPanel",
    "workflowHistoryList",
    "refreshWorkflowHistory",
    "quickStartProgress",
    "quickStartSource",
    "quickStartConfigure",
    "quickStartQueue",
    "quickStartRun",
    "replayWorkflow",
    "replayWorkflowFile",
    "exportWorkflow",
    "clearWorkflow",
    "validateTask",
    "runWorkflow",
    "queueCount",
    "taskQueue",
    "workbenchTabs",
    "editorPane",
    "inspectorPane",
    "editorMode",
    "editorTitle",
    "editorBackend",
    "variantSelectBlock",
    "variantSelect",
    "componentSummary",
    "componentChips",
    "importTask",
    "importTaskFile",
    "editorTabs",
    "toggleAdvancedParameters",
    "parameterForm",
    "jsonEditor",
    "validationMessage",
    "saveTask",
    "workflowStatus",
    "progressBlock",
    "progressLabel",
    "progressPercent",
    "progressFill",
    "inspectorEmpty",
    "resultContent",
    "resultTaskKind",
    "resultTitle",
    "resultBackend",
    "taskResultTabs",
    "errorSection",
    "errorBox",
    "handoverSection",
    "handoverSummary",
    "verificationSection",
    "verificationSummary",
    "metricSection",
    "sampleCount",
    "metricGrid",
    "plotSection",
    "plotGallery",
    "eventSection",
    "eventList",
    "modelSection",
    "modelSummary",
    "modelBody",
    "artifactSection",
    "artifactList",
    "toastRegion",
  ];
  for (const id of ids) {
    elements[id] = document.getElementById(id);
  }
}

function bindEvents() {
  elements.languageSwitcher.addEventListener("click", (event) => {
    const button = event.target.closest("[data-locale]");
    if (button) {
      setLocale(button.dataset.locale);
    }
  });

  elements.sourceViewTabs.addEventListener("click", (event) => {
    const button = event.target.closest("[data-source-view]");
    if (button) {
      setSourceView(button.dataset.sourceView);
    }
  });

  elements.libraryTabs.addEventListener("click", (event) => {
    const button = event.target.closest("[data-library-mode]");
    if (button) {
      setLibraryMode(button.dataset.libraryMode);
    }
  });

  elements.assistantPrompt.addEventListener("input", () => updateControls());
  elements.generateAssistantDraft.addEventListener(
    "click",
    generateNaturalLanguageDraft,
  );
  elements.newAssistantSession.addEventListener(
    "click",
    resetAssistantSession,
  );
  elements.applyAssistantDraft.addEventListener(
    "click",
    applyNaturalLanguageDraft,
  );
  elements.confirmAndRunAssistantDraft.addEventListener(
    "click",
    confirmAndRunNaturalLanguageDraft,
  );

  elements.scenarioSearch.addEventListener("input", (event) => {
    state.searchText = event.target.value.trim().toLowerCase();
    renderLibrary();
  });

  elements.backendFilters.addEventListener("click", (event) => {
    const button = event.target.closest("[data-backend-filter]");
    if (!button) {
      return;
    }
    state.activeBackend = button.dataset.backendFilter;
    renderBackendFilters();
    renderLibrary();
  });

  elements.libraryList.addEventListener("click", (event) => {
    const familyCard = event.target.closest("[data-family-id]");
    if (familyCard) {
      selectTaskFamily(familyCard.dataset.familyId);
      return;
    }
    const card = event.target.closest("[data-scenario-id]");
    if (card) {
      selectScenario(card.dataset.scenarioId);
    }
  });

  elements.variantSelect.addEventListener("change", (event) => {
    loadFamilyVariant(event.target.value);
  });

  elements.editorTabs.addEventListener("click", (event) => {
    const button = event.target.closest("[data-editor-view]");
    if (button) {
      setEditorView(button.dataset.editorView);
    }
  });
  elements.toggleAdvancedParameters.addEventListener("click", () => {
    state.showAdvancedParameters = !state.showAdvancedParameters;
    const document = parseEditorDocumentSilently();
    if (document) {
      renderParameterForm(document);
    }
    renderEditorView();
  });
  elements.workbenchTabs.addEventListener("click", (event) => {
    const button = event.target.closest("[data-workbench-view]");
    if (button) {
      setWorkbenchView(button.dataset.workbenchView);
    }
  });
  elements.parameterForm.addEventListener("input", updateDocumentFromForm);
  elements.parameterForm.addEventListener("change", updateDocumentFromForm);
  elements.importTask.addEventListener("click", () => {
    if (!isWorkflowActive()) {
      elements.importTaskFile.click();
    }
  });
  elements.importTaskFile.addEventListener("change", importTaskDocument);

  elements.saveTask.addEventListener("click", saveEditorTask);
  elements.validateTask.addEventListener("click", validateEditor);
  elements.replayWorkflow.addEventListener("click", () => {
    if (!isWorkflowActive() && state.tasks.length === 0) {
      elements.replayWorkflowFile.click();
    } else if (state.tasks.length > 0) {
      toast(t("workflowReplayRequiresEmptyQueue"), "error");
    }
  });
  elements.replayWorkflowFile.addEventListener(
    "change",
    replayWorkflowDocument,
  );
  elements.exportWorkflow.addEventListener(
    "click",
    exportWorkflowDocument,
  );
  elements.toggleWorkflowHistory.addEventListener(
    "click",
    toggleWorkflowHistory,
  );
  elements.refreshWorkflowHistory.addEventListener(
    "click",
    loadWorkflowHistory,
  );
  elements.workflowHistoryList.addEventListener(
    "click",
    handleWorkflowHistoryAction,
  );
  elements.clearWorkflow.addEventListener("click", clearWorkflow);
  elements.runWorkflow.addEventListener("click", runWorkflow);

  elements.taskQueue.addEventListener("click", (event) => {
    const actionButton = event.target.closest("[data-task-action]");
    if (actionButton) {
      event.stopPropagation();
      handleTaskAction(
        actionButton.dataset.taskAction,
        actionButton.dataset.taskId,
      );
      return;
    }
    const card = event.target.closest("[data-task-id]");
    if (card) {
      selectTask(card.dataset.taskId);
    }
  });
  elements.taskQueue.addEventListener("change", (event) => {
    const field = event.target.closest("[data-handover-field]");
    if (!field || isWorkflowActive()) {
      return;
    }
    const task = state.tasks.find(
      (item) => item.task_id === field.dataset.taskId,
    );
    if (!task?.handover) {
      return;
    }
    event.stopPropagation();
    if (field.dataset.handoverField === "source_event") {
      task.handover.source_event = field.value;
    } else if (
      field.dataset.handoverField === "launch_epoch_s_since_j2000"
    ) {
      const epoch = Number(field.value);
      if (!Number.isFinite(epoch)) {
        field.classList.add("invalid");
        toast(t("handoverInvalidEpoch"), "error");
        return;
      }
      field.classList.remove("invalid");
      task.handover.launch_epoch_s_since_j2000 = epoch;
    }
    renderQueue();
  });

  elements.taskResultTabs.addEventListener("click", (event) => {
    const tab = event.target.closest("[data-result-task-id]");
    if (tab) {
      state.selectedResultTaskId = tab.dataset.resultTaskId;
      renderInspector();
    }
  });

  elements.jsonEditor.addEventListener("input", () => {
    elements.jsonEditor.classList.remove("invalid");
    setValidationMessage(t("validationDirty"), "");
  });
}

async function initialize() {
  try {
    const [
      health,
      capabilities,
      taskFamilies,
      scenarios,
      parameterDefinitions,
      assistantStatus,
      assistantSession,
      restoredWorkflow,
    ] = await Promise.all([
        api("/api/health"),
        api("/api/capabilities"),
        api("/api/task-families"),
        api("/api/scenarios"),
        api("/api/parameter-definitions"),
        api("/api/assistant/status"),
        restoreAssistantSession(),
        restoreActiveWorkflow(),
      ]);
    parameterGuide = parameterDefinitions;
    state.capabilities = capabilities.backends;
    state.taskFamilies = taskFamilies.task_families;
    state.scenarios = scenarios.scenarios;
    state.assistantStatus = assistantStatus;
    if (assistantSession?.locale === state.locale) {
      state.assistantSession = assistantSession;
      state.assistantDraft = assistantSession.draft;
    } else if (assistantSession) {
      rememberAssistantSession(null);
    }
    if (!assistantStatus.available && !state.assistantSession) {
      state.sourceView = "catalog";
    }
    state.serviceState = "online";
    elements.serviceDot.classList.add("online");
    renderServiceStatus();
    elements.backendCount.textContent = String(state.capabilities.length);
    elements.protocolVersion.textContent = `v${health.protocol_version}`;
    renderSourceView();
    renderWorkbenchView();
    renderAssistant();
    renderBackendFilters();
    renderLibrary();
    renderWorkflowHistory();
    if (restoredWorkflow) {
      await applyRestoredWorkflow(restoredWorkflow);
      renderQueue();
      renderInspector();
      if (!isTerminalStatus(restoredWorkflow.status)) {
        pollWorkflow();
      }
    } else if (state.taskFamilies.length > 0) {
      const defaultFamily =
        state.taskFamilies.find(
          (item) => item.family_id === "rocket_flight",
        ) || state.taskFamilies[0];
      await selectTaskFamily(defaultFamily.family_id);
    }
  } catch (error) {
    state.serviceState = "offline";
    elements.serviceDot.classList.add("offline");
    renderServiceStatus();
    elements.libraryList.innerHTML = emptyMarkup(
      t("loadTaskCatalogFailed"),
      error.message,
    );
    toast(error.message, "error");
  }
  updateControls();
}

async function generateNaturalLanguageDraft() {
  if (isWorkflowActive() || state.assistantBusy) {
    toast(t("workflowActiveTaskLocked"), "error");
    return;
  }
  const prompt = elements.assistantPrompt.value.trim();
  if (!prompt) {
    toast(t("assistantPromptRequired"), "error");
    elements.assistantPrompt.focus();
    return;
  }
  state.assistantBusy = true;
  state.assistantError = null;
  if (!state.assistantSession) {
    state.assistantDraft = null;
  }
  renderAssistant();
  updateControls();
  try {
    const session = state.assistantSession;
    const updated = session
      ? await api(
          `/api/assistant/sessions/${encodeURIComponent(session.session_id)}/turns`,
          {
            method: "POST",
            body: JSON.stringify({
              message: prompt,
              expected_revision: session.revision,
            }),
          },
        )
      : await api("/api/assistant/sessions", {
          method: "POST",
          body: JSON.stringify({ prompt, locale: state.locale }),
        });
    state.assistantSession = updated;
    state.assistantDraft = updated.draft;
    rememberAssistantSession(updated.session_id);
    elements.assistantPrompt.value = "";
  } catch (error) {
    state.assistantError = error.message;
    if (!state.assistantSession) {
      state.assistantDraft = {
        status: "error",
        message: error.message,
        questions: [],
        assumptions: [],
        patches: [],
      };
    }
  }
  state.assistantBusy = false;
  renderAssistant();
  updateControls();
}

function resetAssistantSession() {
  if (state.assistantBusy) {
    return;
  }
  const session = state.assistantSession;
  state.assistantSession = null;
  state.assistantDraft = null;
  state.assistantError = null;
  rememberAssistantSession(null);
  elements.assistantPrompt.value = "";
  renderAssistant();
  updateControls();
  if (session) {
    api(
      `/api/assistant/sessions/${encodeURIComponent(session.session_id)}?expected_revision=${session.revision}`,
      { method: "DELETE" },
    ).catch(() => {
      // Sessions are bounded and expire automatically if the server is unavailable.
    });
  }
}

async function applyNaturalLanguageDraft() {
  const draft = state.assistantDraft;
  if (
    isWorkflowActive() ||
    draft?.status !== "proposal" ||
    !draft.draft_document
  ) {
    return;
  }
  try {
    const family = await api(
      `/api/task-families/${encodeURIComponent(draft.family_id)}`,
    );
    if (
      !family.variants.some(
        (variant) => variant.variant_id === draft.variant_id,
      )
    ) {
      throw new Error(t("variantNotFound"));
    }
    state.selectedFamilyId = draft.family_id;
    state.selectedFamilyDetail = family;
    state.selectedVariantId = draft.variant_id;
    state.selectedScenarioId = null;
    state.selectedTaskId = null;
    state.editorMode = "new";
    setWorkbenchView("editor");
    renderFamilyVariantControl();
    setEditorDocument(
      JSON.parse(JSON.stringify(draft.draft_document)),
      {
        familyId: draft.family_id,
        backend: draft.validation?.backend?.backend_id || "",
      },
    );
    setValidationMessage(t("assistantDraftAppliedValidation"), "success");
    state.assistantDraft = { ...draft, applied: true };
    renderAssistant();
    renderLibrary();
    renderQueue();
    toast(t("assistantDraftApplied"), "success");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function confirmAndRunNaturalLanguageDraft() {
  const draft = state.assistantDraft;
  const session = state.assistantSession;
  if (
    isWorkflowActive() ||
    state.assistantBusy ||
    draft?.status !== "proposal" ||
    !draft.draft_document ||
    !session ||
    session.execution
  ) {
    return;
  }
  if (state.tasks.length > 0) {
    toast(t("assistantExecutionRequiresEmptyQueue"), "error");
    return;
  }

  state.assistantBusy = true;
  state.assistantError = null;
  renderAssistant();
  updateControls();
  try {
    const response = await api(
      `/api/assistant/sessions/${encodeURIComponent(session.session_id)}/executions`,
      {
        method: "POST",
        body: JSON.stringify({
          expected_revision: session.revision,
          confirmed: true,
        }),
      },
    );
    const confirmedSession = response.session;
    const workflow = response.workflow;
    const taskId = confirmedSession.execution.task_id;
    state.assistantSession = confirmedSession;
    state.assistantDraft = {
      ...confirmedSession.draft,
      executed: true,
    };
    state.tasks = [
      {
        task_id: taskId,
        document: JSON.parse(JSON.stringify(draft.draft_document)),
        handover: null,
      },
    ];
    state.selectedTaskId = taskId;
    state.workflow = workflow;
    state.selectedResultTaskId = taskId;
    elements.workflowName.value = workflow.name;
    rememberAssistantSession(confirmedSession.session_id);
    rememberActiveWorkflow(workflow.workflow_id);
    setWorkbenchView("results");
    renderQueue();
    renderInspector();
    toast(t("assistantExecutionSubmitted"), "success");
    pollWorkflow();
  } catch (error) {
    state.assistantError = error.message;
    toast(error.message, "error");
  }
  state.assistantBusy = false;
  renderAssistant();
  updateControls();
}

function renderAssistant() {
  const configured = Boolean(state.assistantStatus?.configured);
  const available = Boolean(state.assistantStatus?.available);
  const session = state.assistantSession;
  elements.assistantPromptCaption.textContent = t(
    session ? "assistantFollowUpLabel" : "assistantPromptLabel",
  );
  elements.assistantPrompt.placeholder = t(
    session ? "assistantFollowUpPlaceholder" : "assistantPromptPlaceholder",
  );
  elements.generateAssistantDraft.textContent = t(
    session ? "assistantContinue" : "assistantGenerate",
  );
  elements.newAssistantSession.hidden = !session;
  renderAssistantConversation();
  elements.assistantStatus.className = "assistant-status";
  elements.assistantStatus.removeAttribute("title");
  if (state.assistantBusy) {
    elements.assistantStatus.classList.add("busy");
    elements.assistantStatus.textContent = t("assistantStatusGenerating");
  } else if (available) {
    elements.assistantStatus.textContent = t("assistantStatusReady");
  } else {
    elements.assistantStatus.classList.add("unavailable");
    elements.assistantStatus.textContent = t(
      configured
        ? "assistantStatusUnhealthy"
        : "assistantStatusUnavailable",
    );
    const detail = state.assistantStatus?.health?.detail;
    if (detail) {
      elements.assistantStatus.title = detail;
    }
  }

  const draft = state.assistantDraft;
  if (!draft) {
    elements.assistantResult.hidden = true;
    elements.assistantResult.replaceChildren();
    elements.applyAssistantDraft.hidden = true;
    elements.confirmAndRunAssistantDraft.hidden = true;
    elements.assistantExecutionNote.hidden = true;
    return;
  }

  const status = draft.status || "error";
  const questions = (draft.questions || [])
    .map((item) => `<li>${escapeHTML(item)}</li>`)
    .join("");
  const assumptions = (draft.assumptions || [])
    .map((item) => `<li>${escapeHTML(item)}</li>`)
    .join("");
  const intentSummary = draft.intent_ir?.task_summary || "";
  const intentGoals = [
    ...(draft.intent_ir?.goals || []),
    ...(draft.intent_ir?.inferred_requirements || []).map(
      (item) => item.concept,
    ),
  ]
    .filter((item, index, items) => item && items.indexOf(item) === index)
    .map((item) => `<li>${escapeHTML(item)}</li>`)
    .join("");
  const decisionBasis = (draft.capability_decision?.decision_basis || [])
    .map((item) => `<li>${escapeHTML(item)}</li>`)
    .join("");
  const capabilityGaps = [
    ...(draft.capability_decision?.capability_gaps || []),
    ...(draft.contract_synthesis?.unmapped_requirements || []),
  ]
    .filter((item, index, items) => item && items.indexOf(item) === index)
    .map((item) => `<li>${escapeHTML(item)}</li>`)
    .join("");
  const patches = (draft.patches || [])
    .map(
      (patch) => `
        <li>
          <code>${escapeHTML(patch.path)}</code>
          = <code>${escapeHTML(patch.value_json)}</code>
        </li>
      `,
    )
    .join("");
  const variant =
    status === "proposal"
      ? localizedTaskVariant(draft.family_id, {
          variant_id: draft.variant_id,
        }).label
      : "";
  elements.assistantResult.className = `assistant-result ${status}`;
  elements.assistantResult.innerHTML = `
    <strong>${escapeHTML(
      status === "proposal"
        ? t("assistantProposalTitle", { variant })
        : t(`assistantResult_${status}`),
    )}</strong>
    <span>${escapeHTML(draft.message || "")}</span>
    ${
      intentSummary
        ? `<span><b>${escapeHTML(t("assistantIntentSummary"))}</b> ${escapeHTML(intentSummary)}</span>`
        : ""
    }
    ${
      intentGoals
        ? `<span>${escapeHTML(t("assistantIntentGoals"))}</span><ul>${intentGoals}</ul>`
        : ""
    }
    ${
      decisionBasis
        ? `<span>${escapeHTML(t("assistantDecisionBasis"))}</span><ul>${decisionBasis}</ul>`
        : ""
    }
    ${
      capabilityGaps
        ? `<span>${escapeHTML(t("assistantCapabilityGaps"))}</span><ul>${capabilityGaps}</ul>`
        : ""
    }
    ${questions ? `<ul>${questions}</ul>` : ""}
    ${
      patches
        ? `<span>${escapeHTML(t("assistantPatchSummary"))}</span><ul>${patches}</ul>`
        : ""
    }
    ${
      assumptions
        ? `<span>${escapeHTML(t("assistantAssumptionSummary"))}</span><ul>${assumptions}</ul>`
        : ""
    }
  `;
  elements.assistantResult.hidden = false;
  const executed = Boolean(draft.executed || session?.execution);
  const executable =
    status === "proposal" &&
    !draft.applied &&
    !executed;
  elements.applyAssistantDraft.hidden =
    status !== "proposal" || Boolean(draft.applied) || executed;
  elements.confirmAndRunAssistantDraft.hidden = !executable;
  elements.assistantExecutionNote.hidden = !executable;
}

function renderAssistantConversation() {
  const session = state.assistantSession;
  if (!session) {
    elements.assistantConversation.hidden = true;
    elements.assistantConversation.replaceChildren();
    return;
  }
  const turns = (session.turns || [])
    .map(
      (turn) => `
        <div class="assistant-turn ${escapeHTML(turn.role)}">
          <span>${escapeHTML(
            t(
              turn.role === "user"
                ? "assistantConversationUser"
                : "assistantConversationAssistant",
            ),
          )}</span>
          <p>${escapeHTML(turn.content).replaceAll("\n", "<br>")}</p>
        </div>
      `,
    )
    .join("");
  const error = state.assistantError
    ? `<div class="assistant-session-error">${escapeHTML(state.assistantError)}</div>`
    : "";
  elements.assistantConversation.innerHTML = `
    <div class="assistant-session-meta">
      ${escapeHTML(
        t("assistantSessionRevision", {
          revision: session.revision,
        }),
      )}
    </div>
    ${turns}
    ${error}
  `;
  elements.assistantConversation.hidden = false;
  elements.assistantConversation.scrollTop =
    elements.assistantConversation.scrollHeight;
}

function renderBackendFilters() {
  const filters = [
    { id: "all", label: t("allBackends") },
    ...state.capabilities.map((item) => ({
      id: item.backend_id,
      label: item.backend_name,
    })),
  ];
  elements.backendFilters.innerHTML = filters
    .map(
      (item) => `
        <button
          class="filter-button ${state.activeBackend === item.id ? "active" : ""}"
          type="button"
          data-backend-filter="${escapeHTML(item.id)}"
        >${escapeHTML(item.label)}</button>
      `,
    )
    .join("");
}

function setSourceView(view) {
  if (!["assistant", "catalog"].includes(view)) {
    return;
  }
  state.sourceView = view;
  renderSourceView();
}

function renderSourceView() {
  for (const button of elements.sourceViewTabs.querySelectorAll(
    "[data-source-view]",
  )) {
    const active = button.dataset.sourceView === state.sourceView;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  }
  elements.assistantSourcePane.hidden = state.sourceView !== "assistant";
  elements.catalogSourcePane.hidden = state.sourceView !== "catalog";
}

function setLibraryMode(mode) {
  if (
    !["families", "templates"].includes(mode) ||
    mode === state.libraryMode
  ) {
    return;
  }
  state.libraryMode = mode;
  state.searchText = "";
  elements.scenarioSearch.value = "";
  renderLibrary();
}

function setWorkbenchView(view) {
  if (!["editor", "results"].includes(view)) {
    return;
  }
  state.workbenchView = view;
  renderWorkbenchView();
}

function renderWorkbenchView() {
  for (const button of elements.workbenchTabs.querySelectorAll(
    "[data-workbench-view]",
  )) {
    const active = button.dataset.workbenchView === state.workbenchView;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  }
  elements.editorPane.hidden = state.workbenchView !== "editor";
  elements.inspectorPane.hidden = state.workbenchView !== "results";
}

function renderLibrary() {
  for (const button of elements.libraryTabs.querySelectorAll(
    "[data-library-mode]",
  )) {
    const active = button.dataset.libraryMode === state.libraryMode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  }
  const familyMode = state.libraryMode === "families";
  elements.libraryTitle.textContent = t(
    familyMode ? "newTask" : "exampleTemplates",
  );
  elements.librarySearchLabel.textContent = t(
    familyMode ? "searchTaskFamilies" : "searchTemplates",
  );
  elements.scenarioSearch.placeholder = t(
    familyMode ? "searchTaskFamiliesPlaceholder" : "searchPlaceholder",
  );
  if (familyMode) {
    renderTaskFamilyList();
  } else {
    renderScenarioList();
  }
}

function renderTaskFamilyList() {
  const filtered = state.taskFamilies.filter((family) => {
    const backendMatch =
      state.activeBackend === "all" ||
      family.backend_ids.includes(state.activeBackend);
    const localized = localizedTaskFamily(family.family_id);
    const haystack = [
      family.family_id,
      family.family_schema,
      family.contract_schema,
      localized.label,
      localized.description,
      ...family.task_kinds,
    ]
      .join(" ")
      .toLowerCase();
    return backendMatch && haystack.includes(state.searchText);
  });
  elements.libraryCount.textContent = String(filtered.length);
  if (filtered.length === 0) {
    elements.libraryList.innerHTML = emptyMarkup(
      t("noMatchingTaskFamilies"),
      t("adjustSearch"),
    );
    return;
  }
  elements.libraryList.innerHTML = filtered
    .map((family) => {
      const localized = localizedTaskFamily(family.family_id);
      const backendId = family.backend_ids[0] || "";
      return `
        <button
          class="scenario-card task-type-card ${
            family.family_id === state.selectedFamilyId
              ? "selected"
              : ""
          }"
          type="button"
          data-family-id="${escapeHTML(family.family_id)}"
        >
          <div class="scenario-card-top">
            <strong>${escapeHTML(localized.label)}</strong>
            ${backendBadge(backendId)}
          </div>
          <p>${escapeHTML(localized.description)}</p>
          <div class="task-type-meta">
            <span>${escapeHTML(
              t(
                family.variant_count === 1
                  ? "variantCountOne"
                  : "variantCountMany",
                { count: family.variant_count },
              ),
            )}</span>
            <span>${escapeHTML(
              t(
                family.component_count === 1
                  ? "componentCountOne"
                  : "componentCountMany",
                { count: family.component_count },
              ),
            )}</span>
          </div>
          <span class="task-kind">${escapeHTML(family.family_schema)}</span>
        </button>
      `;
    })
    .join("");
}

function renderScenarioList() {
  const filtered = state.scenarios.filter((scenario) => {
    const backendMatch =
      state.activeBackend === "all" ||
      scenario.backend_id === state.activeBackend;
    const haystack = [
      scenario.label,
      scenario.description,
      localizedScenarioLabel(scenario),
      localizedScenarioDescription(scenario),
      scenario.task_kind,
      scenario.backend_id,
    ]
      .join(" ")
      .toLowerCase();
    return backendMatch && haystack.includes(state.searchText);
  });
  elements.libraryCount.textContent = String(filtered.length);
  if (filtered.length === 0) {
    elements.libraryList.innerHTML = emptyMarkup(
      t("noMatchingTemplates"),
      t("adjustSearch"),
    );
    return;
  }
  elements.libraryList.innerHTML = filtered
    .map(
      (scenario) => `
        <button
          class="scenario-card ${
            scenario.scenario_id === state.selectedScenarioId ? "selected" : ""
          }"
          type="button"
          data-scenario-id="${escapeHTML(scenario.scenario_id)}"
        >
          <div class="scenario-card-top">
            <strong>${escapeHTML(localizedScenarioLabel(scenario))}</strong>
            ${backendBadge(scenario.backend_id)}
          </div>
          <p>${escapeHTML(localizedScenarioDescription(scenario))}</p>
          <span class="task-kind">${escapeHTML(scenario.task_kind)}</span>
        </button>
      `,
    )
    .join("");
}

async function selectTaskFamily(familyId) {
  if (isWorkflowActive()) {
    toast(t("workflowActiveTaskLocked"), "error");
    return;
  }
  try {
    const family = await api(
      `/api/task-families/${encodeURIComponent(familyId)}`,
    );
    state.selectedFamilyId = familyId;
    state.selectedFamilyDetail = family;
    state.selectedVariantId = family.default_variant_id;
    state.selectedScenarioId = null;
    state.selectedTaskId = null;
    state.editorMode = "new";
    loadFamilyVariant(family.default_variant_id);
  } catch (error) {
    toast(error.message, "error");
  }
}

function loadFamilyVariant(variantId) {
  if (isWorkflowActive()) {
    toast(t("workflowActiveTaskLocked"), "error");
    return;
  }
  const family = state.selectedFamilyDetail;
  const variant = family?.variants.find(
    (item) => item.variant_id === variantId,
  );
  if (!family || !variant) {
    toast(t("variantNotFound"), "error");
    return;
  }
  const document = JSON.parse(JSON.stringify(variant.starter_document));
  document.name = `custom_${family.family_id}_${variant.variant_id}`;
  document.description =
    `User-configured ${family.family_id} ${variant.variant_id} task.`;
  state.selectedVariantId = variantId;
  state.selectedScenarioId = null;
  state.selectedTaskId = null;
  state.editorMode = "new";
  setWorkbenchView("editor");
  renderFamilyVariantControl();
  setEditorDocument(document, {
    familyId: family.family_id,
    backend: family.backend_ids[0],
  });
  renderLibrary();
  renderQueue();
}

function renderFamilyVariantControl() {
  const family = state.selectedFamilyDetail;
  const visible = Boolean(family && state.editorMode === "new");
  elements.variantSelectBlock.hidden = !visible;
  elements.componentSummary.hidden = !visible;
  if (!visible) {
    elements.variantSelect.replaceChildren();
    elements.componentChips.replaceChildren();
    return;
  }
  elements.variantSelect.innerHTML = family.variants
    .map(
      (variant) => `
        <option
          value="${escapeHTML(variant.variant_id)}"
          ${variant.variant_id === state.selectedVariantId ? "selected" : ""}
        >${escapeHTML(localizedTaskVariant(family.family_id, variant).label)}</option>
      `,
    )
    .join("");
  const variant = family.variants.find(
    (item) => item.variant_id === state.selectedVariantId,
  );
  elements.componentChips.innerHTML = (variant?.component_ids || [])
    .map(
      (componentId) =>
        `<span class="component-chip" role="listitem">${
          escapeHTML(componentId)
        }</span>`,
    )
    .join("");
}

async function selectScenario(scenarioId) {
  if (isWorkflowActive()) {
    toast(t("workflowActiveTaskLocked"), "error");
    return;
  }
  try {
    const item = await api(`/api/scenarios/${encodeURIComponent(scenarioId)}`);
    state.selectedFamilyId = null;
    state.selectedFamilyDetail = null;
    state.selectedVariantId = null;
    state.selectedScenarioId = scenarioId;
    state.selectedTaskId = null;
    state.editorMode = "template";
    setWorkbenchView("editor");
    renderFamilyVariantControl();
    setEditorDocument(item.document, {
      scenarioId: item.scenario_id,
      backend: item.backend_id,
    });
    renderLibrary();
    renderQueue();
  } catch (error) {
    toast(error.message, "error");
  }
}

function selectTask(taskId) {
  const task = state.tasks.find((item) => item.task_id === taskId);
  if (!task) {
    return;
  }
  state.selectedTaskId = taskId;
  state.editorMode = "task";
  setWorkbenchView("editor");
  renderFamilyVariantControl();
  setEditorDocument(task.document, {
    taskNumber: taskIndex(taskId) + 1,
    backend: taskBackend(task.document),
  });
  renderQueue();
}

function setEditorDocument(document, metadata) {
  elements.jsonEditor.value = JSON.stringify(document, null, 2);
  state.showAdvancedParameters = false;
  renderParameterForm(document);
  if (state.editorMode === "task") {
    elements.editorMode.textContent = t("taskMode", {
      number: String(metadata.taskNumber || 1).padStart(2, "0"),
    });
  } else {
    elements.editorMode.textContent = t(
      state.editorMode === "template" ? "templateMode" : "newTaskMode",
    );
  }
  const scenario = state.scenarios.find(
    (item) => item.scenario_id === metadata.scenarioId,
  );
  const family = state.taskFamilies.find(
    (item) => item.family_id === metadata.familyId,
  );
  elements.editorTitle.textContent = scenario
    ? localizedScenarioLabel(scenario)
    : family
      ? localizedTaskFamily(family.family_id).label
      : taskLabel(document);
  setBackendBadgeElement(elements.editorBackend, metadata.backend);
  elements.saveTask.textContent =
    state.editorMode === "task"
      ? t("validateAndUpdate")
      : t("validateAndAdd");
  setValidationMessage(t("validationDefault"), "");
  elements.jsonEditor.classList.remove("invalid");
  updateControls();
}

function setEditorView(view) {
  if (!["form", "json"].includes(view) || view === state.editorView) {
    return;
  }
  if (view === "form") {
    const document = parseEditorDocument();
    if (!document) {
      return;
    }
    renderParameterForm(document);
  }
  state.editorView = view;
  renderEditorView();
}

function renderEditorView() {
  for (const button of elements.editorTabs.querySelectorAll(
    "[data-editor-view]",
  )) {
    const active = button.dataset.editorView === state.editorView;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  }
  elements.parameterForm.hidden = state.editorView !== "form";
  elements.jsonEditor.hidden = state.editorView !== "json";
  elements.toggleAdvancedParameters.hidden = state.editorView !== "form";
  elements.toggleAdvancedParameters.textContent = t(
    state.showAdvancedParameters
      ? "hideAdvancedParameters"
      : "showAdvancedParameters",
  );
  elements.toggleAdvancedParameters.setAttribute(
    "aria-pressed",
    String(state.showAdvancedParameters),
  );
}

function renderParameterForm(document) {
  const sections = [];
  const identity = {};
  for (const [key, value] of Object.entries(document)) {
    if (
      [
        "schema_version",
        "name",
        "description",
        "backend",
        "dynamics",
        "protocol_version",
        "request_id",
        "label",
        "task_kind",
        "contract_schema",
        "backend_preference",
      ].includes(key)
    ) {
      identity[key] = value;
    } else {
      sections.push([key, value]);
    }
  }
  const familyId = activeDocumentFamilyId(document);
  const basicSectionNames = new Set(
    parameterGuide?.basicSections?.[familyId] || [],
  );
  if (
    familyId === "orbit_propagation" &&
    document.aerodynamics?.enabled !== true
  ) {
    basicSectionNames.delete("aerodynamics");
  }
  const hasGuidedSections = basicSectionNames.size > 0;
  const allSections = [
    ["identity", identity, []],
    ...sections.map(([key, value]) => [key, value, [key]]),
  ];
  const hiddenAdvancedCount = allSections.filter(
    ([key]) =>
      key === "identity" ||
      (hasGuidedSections && !basicSectionNames.has(key)),
  ).length;
  const visibleSections = allSections.filter(
    ([key]) =>
      state.showAdvancedParameters ||
      (key !== "identity" &&
        (!hasGuidedSections || basicSectionNames.has(key))),
  );
  const markup = `
    <div class="parameter-form-intro">
      <div>
        <strong>${escapeHTML(t("parameterDefaultsReadyTitle"))}</strong>
        <p>${escapeHTML(t("parameterDefaultsReadyHelp"))}</p>
      </div>
      <div class="parameter-form-legend">
        <span class="field-badge recommended">${escapeHTML(
          t("parameterRecommended"),
        )}</span>
        <span>${escapeHTML(t("parameterRecommendedHelp"))}</span>
        ${
          hiddenAdvancedCount > 0 && !state.showAdvancedParameters
            ? `<span>${escapeHTML(
                t("advancedSectionsHidden", {
                  count: hiddenAdvancedCount,
                }),
              )}</span>`
            : ""
        }
      </div>
    </div>
    ${visibleSections
      .map(([key, value, path]) =>
        formSection(key, value, path, {
          advanced:
            key === "identity" ||
            (hasGuidedSections && !basicSectionNames.has(key)),
          familyId,
        }),
      )
      .join("")}
  `;
  elements.parameterForm.innerHTML =
    markup ||
    emptyMarkup(t("noEditableParameters"), t("selectTaskSource"));
  renderEditorView();
}

function formSection(sectionKey, value, path, options) {
  const guide = localizedParameterSection(sectionKey);
  const title =
    sectionKey === "identity"
      ? t("taskIdentity")
      : guide?.label || sectionKey;
  const help = guide?.help || t("parameterSectionGenericHelp");
  let fields;
  if (isPlainObject(value)) {
    fields = Object.entries(value)
      .map(([key, child]) =>
        formField(
          path.length > 0 ? [...path, key] : [key],
          child,
          options.familyId,
        ),
      )
      .join("");
  } else {
    fields = formField(path, value, options.familyId);
  }
  return `
    <details
      class="form-section ${options.advanced ? "advanced" : ""}"
      ${options.advanced ? "" : "open"}
    >
      <summary class="form-section-summary">
        <span>
          <strong>${escapeHTML(title)}</strong>
          <small>${escapeHTML(help)}</small>
        </span>
        <code>${escapeHTML(
          sectionKey === "identity" ? t("systemFields") : sectionKey,
        )}</code>
      </summary>
      <div class="form-grid">${fields}</div>
    </details>
  `;
}

function formField(path, value, familyId) {
  if (isPlainObject(value)) {
    return Object.entries(value)
      .map(([key, child]) => formField([...path, key], child, familyId))
      .join("");
  }
  const fieldPath = path.join(".");
  const locked =
    [
      "schema_version",
      "protocol_version",
      "backend",
      "dynamics",
      "task_kind",
      "contract_schema",
      "backend_preference",
    ].includes(path.at(-1)) ||
    activeVariantSelectorPaths().has(fieldPath);
  const metadata = localizedParameterField(fieldPath, familyId);
  const recommended = new Set(
    parameterGuide?.recommendedPaths?.[familyId] || [],
  ).has(fieldPath);
  const fieldId = `field-help-${fieldPath.replaceAll(/[^a-zA-Z0-9_-]/g, "-")}`;
  const unit = metadata.unit && metadata.unit !== "1" ? metadata.unit : "";
  const range = parameterRangeText(metadata);
  const badges = `
    ${unit ? `<span class="field-badge unit">${escapeHTML(unit)}</span>` : ""}
    ${
      recommended
        ? `<span class="field-badge recommended">${escapeHTML(
            t("parameterRecommended"),
          )}</span>`
        : ""
    }
    ${
      locked
        ? `<span class="field-badge locked">${escapeHTML(
            t("parameterSystemManaged"),
          )}</span>`
        : ""
    }
  `;
  const heading = `
    <span class="form-field-heading">
      <strong>${escapeHTML(metadata.label)}</strong>
      <span>${badges}</span>
    </span>
    <small class="form-field-help" id="${escapeHTML(fieldId)}">
      ${escapeHTML(metadata.help)}
    </small>
  `;
  const footer = `
    <span class="form-field-meta">
      <code>${escapeHTML(fieldPath)}</code>
      ${range ? `<small>${escapeHTML(range)}</small>` : ""}
    </span>
  `;
  const ruleAttributes = parameterRuleAttributes(metadata);
  if (Array.isArray(value)) {
    return `
      <label class="form-field wide ${recommended ? "recommended" : ""}">
        ${heading}
        <textarea
          data-field-path="${escapeHTML(fieldPath)}"
          data-value-type="json"
          spellcheck="false"
          aria-describedby="${escapeHTML(fieldId)}"
          ${locked ? "readonly" : ""}
        >${escapeHTML(JSON.stringify(value, null, 2))}</textarea>
        ${footer}
      </label>
    `;
  }
  if (typeof value === "boolean") {
    return `
      <label class="form-field ${recommended ? "recommended" : ""}">
        ${heading}
        <span class="boolean-control">
          <input
            type="checkbox"
            data-field-path="${escapeHTML(fieldPath)}"
            data-value-type="boolean"
            aria-describedby="${escapeHTML(fieldId)}"
            ${value ? "checked" : ""}
            ${locked ? "disabled" : ""}
          />
          <span>${escapeHTML(
            t(value ? "parameterEnabled" : "parameterDisabled"),
          )}</span>
        </span>
        ${footer}
      </label>
    `;
  }
  if (typeof value === "number") {
    return `
      <label class="form-field ${recommended ? "recommended" : ""}">
        ${heading}
        <input
          type="number"
          ${ruleAttributes}
          value="${escapeHTML(value)}"
          data-field-path="${escapeHTML(fieldPath)}"
          data-value-type="number"
          aria-describedby="${escapeHTML(fieldId)}"
          ${locked ? "readonly" : ""}
        />
        ${footer}
      </label>
    `;
  }
  const wide = ["description", "label"].includes(path.at(-1));
  return `
    <label class="form-field ${wide ? "wide" : ""} ${
      recommended ? "recommended" : ""
    }">
      ${heading}
      <input
        type="text"
        value="${escapeHTML(value ?? "")}"
        data-field-path="${escapeHTML(fieldPath)}"
        data-value-type="string"
        aria-describedby="${escapeHTML(fieldId)}"
        ${locked ? "readonly" : ""}
      />
      ${footer}
    </label>
  `;
}

function activeDocumentFamilyId(document) {
  if (state.selectedFamilyId) {
    return state.selectedFamilyId;
  }
  const taskKind = document.dynamics || document.task_kind;
  const backend = document.backend || document.backend_preference;
  return (
    state.taskFamilies.find(
      (family) =>
        family.task_kinds?.includes(taskKind) &&
        (!backend || family.backend_ids?.includes(backend)),
    )?.family_id || ""
  );
}

function activeFamilyMetadata(familyId) {
  if (state.selectedFamilyDetail?.family_id === familyId) {
    return state.selectedFamilyDetail;
  }
  return state.taskFamilies.find((family) => family.family_id === familyId);
}

function localizedParameterSection(sectionKey) {
  const entry = parameterGuide?.sections?.[sectionKey];
  return entry?.[state.locale] || entry?.en || null;
}

function localizedParameterField(fieldPath, familyId) {
  const leaf = fieldPath.split(".").at(-1);
  const base = parameterGuide?.fields?.[leaf] || {};
  const exact = parameterGuide?.fields?.[fieldPath] || {};
  const baseText = base[state.locale] || base.en || {};
  const exactText = exact[state.locale] || exact.en || {};
  const assistant = activeFamilyMetadata(familyId)?.assistant_parameters?.find(
    (parameter) => parameter.path === fieldPath,
  );
  const assistantHelp =
    state.locale === "zh-CN"
      ? assistant?.description_zh
      : assistant?.description_en;
  const fallbackLabel =
    state.locale === "zh-CN"
      ? t("customParameterLabel")
      : leaf.replaceAll("_", " ");
  return {
    ...base,
    ...exact,
    label: exactText.label || baseText.label || fallbackLabel,
    help:
      exactText.help ||
      baseText.help ||
      assistantHelp ||
      t("parameterGenericHelp"),
    unit: exact.unit || base.unit || assistant?.unit || "",
  };
}

function parameterRangeText(metadata) {
  const hasMinimum = Number.isFinite(metadata.min);
  const hasMaximum = Number.isFinite(metadata.max);
  const unit = metadata.unit && metadata.unit !== "1" ? ` ${metadata.unit}` : "";
  if (hasMinimum && hasMaximum) {
    let key = "parameterRangeBetween";
    if (metadata.minExclusive && metadata.maxExclusive) {
      key = "parameterRangeOpen";
    } else if (metadata.minExclusive) {
      key = "parameterRangeMinExclusive";
    } else if (metadata.maxExclusive) {
      key = "parameterRangeMaxExclusive";
    }
    return t(key, {
      minimum: formatParameterBound(metadata.min),
      maximum: formatParameterBound(metadata.max),
      unit,
    });
  }
  if (hasMinimum) {
    return t(metadata.minExclusive ? "parameterGreaterThan" : "parameterMinimum", {
      minimum: formatParameterBound(metadata.min),
      unit,
    });
  }
  if (hasMaximum) {
    return t(metadata.maxExclusive ? "parameterLessThan" : "parameterMaximum", {
      maximum: formatParameterBound(metadata.max),
      unit,
    });
  }
  return "";
}

function parameterRuleAttributes(metadata) {
  const attributes = [
    `step="${escapeHTML(metadata.step ?? "any")}"`,
  ];
  const inputMinimum = metadata.inputMin ?? metadata.min;
  const inputMaximum = metadata.inputMax ?? metadata.max;
  if (Number.isFinite(inputMinimum)) {
    attributes.push(`min="${escapeHTML(inputMinimum)}"`);
  }
  if (Number.isFinite(inputMaximum)) {
    attributes.push(`max="${escapeHTML(inputMaximum)}"`);
  }
  return attributes.join(" ");
}

function formatParameterBound(value) {
  return new Intl.NumberFormat(
    state.locale === "zh-CN" ? "zh-CN" : "en-US",
    { maximumFractionDigits: 9 },
  ).format(value);
}

function activeVariantSelectorPaths() {
  const family = state.selectedFamilyDetail;
  const variant = family?.variants?.find(
    (item) => item.variant_id === state.selectedVariantId,
  );
  return new Set(
    (variant?.selectors || []).map((selector) => selector.path),
  );
}

function updateDocumentFromForm(event) {
  const field = event.target.closest("[data-field-path]");
  if (!field) {
    return;
  }
  let document;
  try {
    document = JSON.parse(elements.jsonEditor.value);
    let value;
    if (field.dataset.valueType === "number") {
      value = Number(field.value);
      if (!Number.isFinite(value)) {
        throw new Error(t("numberRequired"));
      }
    } else if (field.dataset.valueType === "boolean") {
      value = field.checked;
    } else if (field.dataset.valueType === "json") {
      value = JSON.parse(field.value);
    } else {
      value = field.value;
    }
    setDocumentPath(document, field.dataset.fieldPath, value);
    elements.jsonEditor.value = JSON.stringify(document, null, 2);
    field.classList.remove("invalid");
    elements.jsonEditor.classList.remove("invalid");
    setValidationMessage(t("validationDirty"), "");
  } catch (error) {
    field.classList.add("invalid");
    setValidationMessage(
      t("parameterParseFailed", { message: error.message }),
      "error",
    );
  }
}

function setDocumentPath(document, path, value) {
  const keys = path.split(".");
  const last = keys.pop();
  let target = document;
  for (const key of keys) {
    target = target[key];
  }
  target[last] = value;
}

async function importTaskDocument() {
  const [file] = elements.importTaskFile.files;
  elements.importTaskFile.value = "";
  if (!file) {
    return;
  }
  try {
    const document = JSON.parse(await file.text());
    if (!isPlainObject(document)) {
      throw new Error(t("jsonRootObject"));
    }
    state.selectedFamilyId = null;
    state.selectedFamilyDetail = null;
    state.selectedVariantId = null;
    state.selectedScenarioId = null;
    state.selectedTaskId = null;
    state.editorMode = "new";
    setWorkbenchView("editor");
    renderFamilyVariantControl();
    setEditorDocument(document, {
      backend: taskBackend(document),
    });
    setValidationMessage(t("importReady"), "success");
    toast(t("importReady"), "success");
    renderLibrary();
  } catch (error) {
    const message = t("importFailed", { message: error.message });
    setValidationMessage(message, "error");
    toast(message, "error");
  }
}

async function saveEditorTask() {
  if (isWorkflowActive()) {
    toast(t("workflowActiveTaskLocked"), "error");
    return;
  }
  const document = parseEditorDocument();
  if (!document) {
    return;
  }
  if (
    state.editorMode !== "task" &&
    !state.selectedTaskId &&
    state.tasks.length >= 12
  ) {
    toast(t("workflowTaskLimit"), "error");
    return;
  }
  elements.saveTask.disabled = true;
  setValidationMessage(t("validatingAndSaving"), "");
  try {
    const validation = await api("/api/validate", {
      method: "POST",
      body: JSON.stringify({ document }),
    });
    if (state.editorMode === "task" && state.selectedTaskId) {
      const task = state.tasks.find(
        (item) => item.task_id === state.selectedTaskId,
      );
      if (task) {
        task.document = document;
        elements.editorTitle.textContent = taskLabel(document);
        setValidationMessage(
          t("taskValidatedAndUpdated", {
            backend: validation.backend.backend_name,
          }),
          "success",
        );
        toast(t("taskUpdated"), "success");
      }
    } else {
      const task = {
        task_id: newTaskId(),
        document,
      };
      state.tasks.push(task);
      state.selectedTaskId = task.task_id;
      state.editorMode = "task";
      renderFamilyVariantControl();
      elements.editorMode.textContent = t("taskMode", {
        number: String(state.tasks.length).padStart(2, "0"),
      });
      elements.editorTitle.textContent = taskLabel(document);
      elements.saveTask.textContent = t("validateAndUpdate");
      setValidationMessage(
        t("taskValidatedAndAdded", {
          backend: validation.backend.backend_name,
        }),
        "success",
      );
      toast(t("taskAdded"), "success");
    }
    elements.jsonEditor.classList.remove("invalid");
  } catch (error) {
    elements.jsonEditor.classList.add("invalid");
    setValidationMessage(error.message, "error");
    toast(error.message, "error");
  }
  if (removeInvalidHandovers()) {
    toast(t("handoverRemovedAfterQueueChange"), "error");
  }
  renderQueue();
  updateControls();
}

function handleTaskAction(action, taskId) {
  if (isWorkflowActive()) {
    toast(t("workflowActiveQueueLocked"), "error");
    return;
  }
  const index = taskIndex(taskId);
  if (index < 0) {
    return;
  }
  if (action === "enable-handover") {
    const source = eligibleHandoverSource(index);
    if (source) {
      state.tasks[index].handover = {
        type: "rocketpy_to_tudatpy",
        source_task_id: source.task_id,
        source_event: "burnout",
        launch_epoch_s_since_j2000: 0,
      };
    }
  } else if (action === "clear-handover") {
    state.tasks[index].handover = null;
  } else if (action === "up" && index > 0) {
    [state.tasks[index - 1], state.tasks[index]] = [
      state.tasks[index],
      state.tasks[index - 1],
    ];
  } else if (action === "down" && index < state.tasks.length - 1) {
    [state.tasks[index + 1], state.tasks[index]] = [
      state.tasks[index],
      state.tasks[index + 1],
    ];
  } else if (action === "remove") {
    state.tasks.splice(index, 1);
    if (state.selectedTaskId === taskId) {
      state.selectedTaskId = null;
      state.editorMode = state.selectedScenarioId ? "template" : "new";
      if (state.selectedScenarioId) {
        selectScenario(state.selectedScenarioId);
      } else if (state.selectedFamilyDetail && state.selectedVariantId) {
        loadFamilyVariant(state.selectedVariantId);
      } else {
        clearEditor();
      }
    }
  }
  if (removeInvalidHandovers()) {
    toast(t("handoverRemovedAfterQueueChange"), "error");
  }
  renderQueue();
  updateControls();
}

function eligibleHandoverSource(targetIndex) {
  const target = state.tasks[targetIndex];
  if (!target || taskBackend(target.document) !== "tudatpy") {
    return null;
  }
  for (let index = targetIndex - 1; index >= 0; index -= 1) {
    if (taskBackend(state.tasks[index].document) === "rocketpy") {
      return state.tasks[index];
    }
  }
  return null;
}

function removeInvalidHandovers() {
  let removed = false;
  for (const [targetIndex, task] of state.tasks.entries()) {
    if (!task.handover) {
      continue;
    }
    const sourceIndex = state.tasks.findIndex(
      (candidate) =>
        candidate.task_id === task.handover.source_task_id,
    );
    const valid =
      task.handover.type === "rocketpy_to_tudatpy" &&
      taskBackend(task.document) === "tudatpy" &&
      sourceIndex >= 0 &&
      sourceIndex < targetIndex &&
      taskBackend(state.tasks[sourceIndex].document) === "rocketpy";
    if (!valid) {
      task.handover = null;
      removed = true;
    }
  }
  return removed;
}

function handoverControls(task, index) {
  const source = eligibleHandoverSource(index);
  if (!task.handover) {
    if (!source) {
      return "";
    }
    return `
      <button
        class="handover-enable"
        type="button"
        data-task-action="enable-handover"
        data-task-id="${escapeHTML(task.task_id)}"
        ${isWorkflowActive() ? "disabled" : ""}
      >
        ${escapeHTML(t("enableRocketOrbitHandover"))}
      </button>
    `;
  }
  const linkedSource = state.tasks.find(
    (candidate) =>
      candidate.task_id === task.handover.source_task_id,
  );
  return `
    <div class="handover-config">
      <div class="handover-heading">
        <strong>${escapeHTML(t("handoverConfiguration"))}</strong>
        <button
          type="button"
          data-task-action="clear-handover"
          data-task-id="${escapeHTML(task.task_id)}"
          ${isWorkflowActive() ? "disabled" : ""}
        >
          ${escapeHTML(t("clearRocketOrbitHandover"))}
        </button>
      </div>
      <small>${escapeHTML(
        t("handoverSource", {
          source: taskLabel(linkedSource?.document || {}),
        }),
      )}</small>
      <label>
        <span>${escapeHTML(t("handoverEvent"))}</span>
        <select
          data-handover-field="source_event"
          data-task-id="${escapeHTML(task.task_id)}"
          ${isWorkflowActive() ? "disabled" : ""}
        >
          <option
            value="burnout"
            ${task.handover.source_event === "burnout" ? "selected" : ""}
          >${escapeHTML(t("handoverBurnout"))}</option>
          <option
            value="apogee"
            ${task.handover.source_event === "apogee" ? "selected" : ""}
          >${escapeHTML(t("handoverApogee"))}</option>
        </select>
      </label>
      <label>
        <span>${escapeHTML(t("handoverLaunchEpoch"))}</span>
        <input
          type="number"
          step="any"
          value="${escapeHTML(
            task.handover.launch_epoch_s_since_j2000,
          )}"
          data-handover-field="launch_epoch_s_since_j2000"
          data-task-id="${escapeHTML(task.task_id)}"
          ${isWorkflowActive() ? "disabled" : ""}
        />
      </label>
    </div>
  `;
}

function renderQueue() {
  elements.queueCount.textContent = `${state.tasks.length} / 12`;
  if (state.tasks.length === 0) {
    elements.taskQueue.className = "task-queue empty-state compact";
    elements.taskQueue.innerHTML = `
      <strong>${escapeHTML(t("queueEmptyTitle"))}</strong>
      <span>${escapeHTML(t("queueEmptyDetail"))}</span>
    `;
    return;
  }
  elements.taskQueue.className = "task-queue";
  const runTasks = new Map(
    (state.workflow?.tasks || []).map((task) => [task.task_id, task]),
  );
  elements.taskQueue.innerHTML = state.tasks
    .map((task, index) => {
      const runTask = runTasks.get(task.task_id);
      const taskStatus = runTask?.status || "queued";
      return `
        <article
          class="task-card ${
            task.task_id === state.selectedTaskId ? "selected" : ""
          }"
          data-task-id="${escapeHTML(task.task_id)}"
          tabindex="0"
        >
          <div class="task-card-top">
            <span class="task-order">${String(index + 1).padStart(2, "0")}</span>
            <div class="task-card-title">
              <strong>${escapeHTML(taskLabel(task.document))}</strong>
              <span>${escapeHTML(taskKind(task.document))}</span>
            </div>
            ${backendBadge(taskBackend(task.document))}
          </div>
          ${handoverControls(task, index)}
          <div class="task-controls">
            <span class="task-status ${escapeHTML(taskStatus)}">
              ${escapeHTML(statusLabel(taskStatus))}
            </span>
            <div class="mini-actions">
              <button
                class="icon-button"
                type="button"
                aria-label="${escapeHTML(t("moveTaskUp"))}"
                title="${escapeHTML(t("moveUp"))}"
                data-task-action="up"
                data-task-id="${escapeHTML(task.task_id)}"
                ${index === 0 || isWorkflowActive() ? "disabled" : ""}
              >&uarr;</button>
              <button
                class="icon-button"
                type="button"
                aria-label="${escapeHTML(t("moveTaskDown"))}"
                title="${escapeHTML(t("moveDown"))}"
                data-task-action="down"
                data-task-id="${escapeHTML(task.task_id)}"
                ${
                  index === state.tasks.length - 1 || isWorkflowActive()
                    ? "disabled"
                    : ""
                }
              >&darr;</button>
              <button
                class="icon-button"
                type="button"
                aria-label="${escapeHTML(t("removeTask"))}"
                title="${escapeHTML(t("remove"))}"
                data-task-action="remove"
                data-task-id="${escapeHTML(task.task_id)}"
                ${isWorkflowActive() ? "disabled" : ""}
              >&times;</button>
            </div>
          </div>
        </article>
      `;
    })
    .join("");
}

async function validateEditor() {
  const document = parseEditorDocument();
  if (!document) {
    return;
  }
  elements.validateTask.disabled = true;
  setValidationMessage(t("validating"), "");
  try {
    const result = await api("/api/validate", {
      method: "POST",
      body: JSON.stringify({ document }),
    });
    setValidationMessage(
      t("validationPassed", {
        backend: `${result.backend.backend_name} ${result.backend.backend_version}`,
        taskKind: result.request.task_kind,
      }),
      "success",
    );
    toast(t("validationPassedToast"), "success");
  } catch (error) {
    elements.jsonEditor.classList.add("invalid");
    setValidationMessage(error.message, "error");
    toast(error.message, "error");
  }
  updateControls();
}

function clearWorkflow() {
  if (isWorkflowActive()) {
    toast(t("workflowActiveClearLocked"), "error");
    return;
  }
  state.tasks = [];
  state.selectedTaskId = null;
  state.workflow = null;
  rememberActiveWorkflow(null);
  state.selectedResultTaskId = null;
  setWorkbenchView("editor");
  renderQueue();
  renderInspector();
  if (state.selectedScenarioId) {
    state.editorMode = "template";
    selectScenario(state.selectedScenarioId);
  } else if (state.selectedFamilyDetail && state.selectedVariantId) {
    state.editorMode = "new";
    loadFamilyVariant(state.selectedVariantId);
  } else {
    state.editorMode = "new";
    clearEditor();
  }
  updateControls();
  toast(t("workflowCleared"), "success");
}

async function exportWorkflowDocument() {
  const workflow = state.workflow;
  if (!workflow || isWorkflowActive()) {
    return;
  }
  elements.exportWorkflow.disabled = true;
  try {
    const document = await api(
      `/api/workflows/${encodeURIComponent(workflow.workflow_id)}/export`,
    );
    const blob = new Blob(
      [`${JSON.stringify(document, null, 2)}\n`],
      { type: "application/json" },
    );
    const url = URL.createObjectURL(blob);
    const link = globalThis.document.createElement("a");
    link.href = url;
    link.download = `workflow-${workflow.workflow_id}.json`;
    globalThis.document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    toast(t("workflowExported"), "success");
  } catch (error) {
    toast(error.message, "error");
  }
  updateControls();
}

async function toggleWorkflowHistory() {
  state.workflowHistoryOpen = !state.workflowHistoryOpen;
  elements.workflowHistoryPanel.hidden = !state.workflowHistoryOpen;
  if (state.workflowHistoryOpen) {
    await loadWorkflowHistory();
  }
}

async function loadWorkflowHistory() {
  elements.workflowHistoryList.innerHTML = emptyMarkup(
    t("workflowHistoryLoading"),
    "",
  );
  try {
    const history = await api("/api/workflows?limit=50");
    state.workflowHistory = history.workflows || [];
    renderWorkflowHistory();
  } catch (error) {
    elements.workflowHistoryList.innerHTML = emptyMarkup(
      t("workflowHistoryLoadFailed"),
      error.message,
    );
  }
}

function renderWorkflowHistory() {
  elements.workflowHistoryPanel.hidden = !state.workflowHistoryOpen;
  if (!state.workflowHistoryOpen) {
    return;
  }
  if (state.workflowHistory.length === 0) {
    elements.workflowHistoryList.innerHTML = emptyMarkup(
      t("workflowHistoryEmpty"),
      t("workflowHistoryEmptyHelp"),
    );
    return;
  }
  elements.workflowHistoryList.innerHTML = state.workflowHistory
    .map((workflow) => {
      const created = new Intl.DateTimeFormat(state.locale, {
        dateStyle: "short",
        timeStyle: "short",
      }).format(new Date(workflow.created_at));
      return `
        <article class="workflow-history-item">
          <button
            class="workflow-history-open"
            type="button"
            data-history-action="open"
            data-workflow-id="${escapeHTML(workflow.workflow_id)}"
          >
            <span class="run-status ${escapeHTML(workflow.status)}">
              ${escapeHTML(statusLabel(workflow.status))}
            </span>
            <strong>${escapeHTML(workflow.name)}</strong>
            <small>
              ${escapeHTML(created)} ·
              ${escapeHTML(t("workflowHistoryTaskCount", {
                count: workflow.task_count,
              }))} ·
              ${escapeHTML(workflow.backends.join(" / "))}
            </small>
          </button>
          <button
            class="icon-button"
            type="button"
            data-history-action="delete"
            data-workflow-id="${escapeHTML(workflow.workflow_id)}"
            title="${escapeHTML(t("workflowHistoryDelete"))}"
            aria-label="${escapeHTML(t("workflowHistoryDelete"))}"
          >&times;</button>
        </article>
      `;
    })
    .join("");
}

async function handleWorkflowHistoryAction(event) {
  const button = event.target.closest("[data-history-action]");
  if (!button) {
    return;
  }
  const workflowId = button.dataset.workflowId;
  if (button.dataset.historyAction === "open") {
    await openWorkflowHistory(workflowId);
  } else if (button.dataset.historyAction === "delete") {
    await deleteWorkflowHistory(workflowId);
  }
}

async function openWorkflowHistory(workflowId) {
  if (isWorkflowActive()) {
    toast(t("workflowHistoryActiveLocked"), "error");
    return;
  }
  try {
    const workflow = await api(
      `/api/workflows/${encodeURIComponent(workflowId)}`,
    );
    await applyRestoredWorkflow(workflow);
    state.workflowHistoryOpen = false;
    renderWorkflowHistory();
    renderQueue();
    renderInspector();
    updateControls();
    if (!isTerminalStatus(workflow.status)) {
      pollWorkflow();
    }
  } catch (error) {
    toast(error.message, "error");
  }
}

async function deleteWorkflowHistory(workflowId) {
  if (state.workflow?.workflow_id === workflowId && isWorkflowActive()) {
    toast(t("workflowHistoryActiveLocked"), "error");
    return;
  }
  const workflow = state.workflowHistory.find(
    (item) => item.workflow_id === workflowId,
  );
  if (
    !globalThis.confirm(
      t("workflowHistoryDeleteConfirm", {
        name: workflow?.name || workflowId,
      }),
    )
  ) {
    return;
  }
  try {
    await api(`/api/workflows/${encodeURIComponent(workflowId)}`, {
      method: "DELETE",
    });
    if (state.workflow?.workflow_id === workflowId) {
      state.workflow = null;
      state.tasks = [];
      state.selectedTaskId = null;
      state.selectedResultTaskId = null;
      rememberActiveWorkflow(null);
      setWorkbenchView("editor");
      clearEditor();
      renderQueue();
      renderInspector();
    }
    toast(t("workflowHistoryDeleted"), "success");
    await loadWorkflowHistory();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function applyRestoredWorkflow(workflow) {
  const exported = await api(
    `/api/workflows/${encodeURIComponent(workflow.workflow_id)}/export`,
  );
  state.workflow = workflow;
  state.tasks = exported.tasks.map((task) => ({
    task_id: task.task_id,
    document: JSON.parse(JSON.stringify(task.document)),
    handover: task.handover
      ? JSON.parse(JSON.stringify(task.handover))
      : null,
  }));
  state.selectedTaskId = state.tasks[0]?.task_id || null;
  state.selectedResultTaskId = workflow.tasks[0]?.task_id || null;
  elements.workflowName.value = workflow.name;
  rememberActiveWorkflow(workflow.workflow_id);
  setWorkbenchView("results");
}

async function replayWorkflowDocument() {
  const [file] = elements.replayWorkflowFile.files;
  elements.replayWorkflowFile.value = "";
  if (!file) {
    return;
  }
  if (isWorkflowActive() || state.tasks.length > 0) {
    toast(t("workflowReplayRequiresEmptyQueue"), "error");
    return;
  }

  updateControls(true);
  try {
    const workflowDocument = JSON.parse(await file.text());
    if (!isPlainObject(workflowDocument)) {
      throw new Error(t("jsonRootObject"));
    }
    const workflow = await api("/api/workflow-replays", {
      method: "POST",
      body: JSON.stringify({
        workflow: workflowDocument,
        confirmed: true,
      }),
    });
    state.tasks = workflowDocument.tasks.map((task) => ({
      task_id: task.task_id,
      document: JSON.parse(JSON.stringify(task.document)),
      handover: task.handover
        ? JSON.parse(JSON.stringify(task.handover))
        : null,
    }));
    state.workflow = workflow;
    rememberActiveWorkflow(workflow.workflow_id);
    state.selectedTaskId = state.tasks[0]?.task_id || null;
    state.selectedResultTaskId = workflow.tasks[0]?.task_id || null;
    elements.workflowName.value = workflow.name;
    setWorkbenchView("results");
    renderQueue();
    renderInspector();
    toast(t("workflowReplaySubmitted"), "success");
    pollWorkflow();
  } catch (error) {
    state.workflow = null;
    rememberActiveWorkflow(null);
    const message = t("workflowReplayFailed", {
      message: error.message,
    });
    toast(message, "error");
    renderInspector();
    updateControls();
  }
}

async function runWorkflow() {
  if (state.tasks.length === 0) {
    toast(t("addTaskFirst"), "error");
    return;
  }
  if (state.editorMode === "task" && state.selectedTaskId) {
    const document = parseEditorDocument();
    if (!document) {
      return;
    }
    const task = state.tasks.find(
      (item) => item.task_id === state.selectedTaskId,
    );
    if (task) {
      task.document = document;
    }
  }
  const workflowName = elements.workflowName.value.trim();
  if (!workflowName) {
    toast(t("workflowNameRequired"), "error");
    elements.workflowName.focus();
    return;
  }
  updateControls(true);
  try {
    state.workflow = await api("/api/workflows", {
      method: "POST",
      body: JSON.stringify({
        name: workflowName,
        tasks: state.tasks,
      }),
    });
    rememberActiveWorkflow(state.workflow.workflow_id);
    state.selectedResultTaskId = state.workflow.tasks[0]?.task_id || null;
    setWorkbenchView("results");
    renderQueue();
    renderInspector();
    toast(t("workflowSubmitted"), "success");
    pollWorkflow();
  } catch (error) {
    toast(error.message, "error");
    state.workflow = null;
    rememberActiveWorkflow(null);
    renderInspector();
    updateControls();
  }
}

async function pollWorkflow() {
  if (!state.workflow || state.polling) {
    return;
  }
  state.polling = true;
  try {
    state.workflow = await api(
      `/api/workflows/${encodeURIComponent(state.workflow.workflow_id)}`,
    );
    rememberActiveWorkflow(state.workflow.workflow_id);
    renderQueue();
    renderInspector();
  } catch (error) {
    toast(error.message, "error");
  }
  state.polling = false;
  if (state.workflow && !isTerminalStatus(state.workflow.status)) {
    window.setTimeout(pollWorkflow, 850);
  } else {
    updateControls();
    if (state.workflow?.status === "completed") {
      toast(t("workflowCompleted"), "success");
    } else if (state.workflow?.status === "failed") {
      toast(t("workflowFailed"), "error");
    } else if (state.workflow?.status === "interrupted") {
      toast(t("workflowInterrupted"), "error");
    }
  }
}

function renderInspector() {
  const workflow = state.workflow;
  if (!workflow) {
    elements.workflowStatus.className = "run-status idle";
    elements.workflowStatus.textContent = statusLabel("idle");
    elements.progressBlock.hidden = true;
    elements.inspectorEmpty.hidden = false;
    elements.resultContent.hidden = true;
    return;
  }

  elements.workflowStatus.className = `run-status ${workflow.status}`;
  elements.workflowStatus.textContent = statusLabel(workflow.status);
  elements.progressBlock.hidden = false;
  const finished = workflow.progress.finished;
  const total = workflow.progress.total;
  const percentage = Math.round(workflow.progress.fraction * 100);
  elements.progressLabel.textContent = `${finished} / ${total}`;
  elements.progressPercent.textContent = `${percentage}%`;
  elements.progressFill.style.width = `${percentage}%`;
  elements.progressFill.parentElement.setAttribute(
    "aria-valuenow",
    String(percentage),
  );

  const selected =
    workflow.tasks.find(
      (task) => task.task_id === state.selectedResultTaskId,
    ) || workflow.tasks[0];
  if (!selected) {
    elements.inspectorEmpty.hidden = false;
    elements.resultContent.hidden = true;
    return;
  }
  state.selectedResultTaskId = selected.task_id;
  elements.inspectorEmpty.hidden = true;
  elements.resultContent.hidden = false;
  renderResultTabs(workflow.tasks);
  renderTaskResult(selected);
}

function renderResultTabs(tasks) {
  elements.taskResultTabs.innerHTML = tasks
    .map(
      (task) => `
        <button
          class="result-tab ${
            task.task_id === state.selectedResultTaskId ? "active" : ""
          }"
          type="button"
          data-result-task-id="${escapeHTML(task.task_id)}"
          title="${escapeHTML(requestLabel(task.request))}"
        >
          ${String(task.order).padStart(2, "0")}
        </button>
      `,
    )
    .join("");
}

function renderTaskResult(task) {
  elements.resultTaskKind.textContent = task.request.task_kind;
  elements.resultTitle.textContent = requestLabel(task.request);
  setBackendBadgeElement(elements.resultBackend, task.backend.backend_id);
  elements.resultBackend.textContent = `${task.backend.backend_name} ${task.backend.backend_version}`;

  elements.errorSection.hidden = !task.error;
  if (task.error) {
    elements.errorBox.textContent = `${task.error.type}: ${task.error.message}`;
  }
  renderHandover(task.handover);

  const summary = task.summary;
  if (!summary) {
    elements.verificationSection.hidden = true;
    elements.verificationSummary.innerHTML = "";
    elements.metricSection.hidden = false;
    elements.metricGrid.innerHTML = emptyMarkup(
      statusLabel(task.status),
      task.status === "running"
        ? t("kernelRunning")
        : t("noResultYet"),
    );
    elements.sampleCount.textContent = "";
    hideResultSections();
    return;
  }

  const metrics = summary.metrics || [];
  renderTargetOrbitVerification(summary);
  const sampleMetric = metrics.find((metric) => metric.name === "sample_count");
  elements.metricSection.hidden = false;
  elements.sampleCount.textContent = sampleMetric
    ? t("samples", { count: formatNumber(sampleMetric.value) })
    : t("samples", { count: summary.time?.sample_count || 0 });
  elements.metricGrid.innerHTML = prioritizedMetrics(metrics)
    .map(
      (metric) => `
        <div class="metric-card" title="${escapeHTML(metric.name)}">
          <span>${escapeHTML(localizedName("metrics", metric.name))}</span>
          <strong>
            ${escapeHTML(formatNumber(metric.value))}
            <small>${escapeHTML(metric.unit)}</small>
          </strong>
        </div>
      `,
    )
    .join("");

  renderPlots(task.artifacts);
  renderEvents(summary.events || []);
  renderModel(summary.model_manifest || {});
  renderArtifacts(task.artifacts);
}

function renderHandover(handover) {
  elements.handoverSection.hidden = !handover;
  if (!handover) {
    elements.handoverSummary.innerHTML = "";
    return;
  }
  const applied = handover.status === "applied";
  const failed = handover.status === "failed";
  elements.handoverSummary.innerHTML = `
    <strong>${escapeHTML(
      t("handoverSource", { source: handover.source_task_id }),
    )}</strong>
    <span>${escapeHTML(
      failed
        ? t("handoverFailed", { message: handover.error })
        : applied
        ? t("handoverApplied", {
            time: formatNumber(handover.source_time_s),
            epoch: formatNumber(handover.target_epoch_s_since_j2000),
          })
        : `${localizedHandoverEvent(handover.source_event)} · J2000 + ${formatNumber(
            handover.launch_epoch_s_since_j2000,
          )} s`,
    )}</span>
    ${
      applied
        ? `<code>${escapeHTML(
            t("handoverTransformModel", {
              model: handover.transform_model,
            }),
          )}</code>`
        : ""
    }
  `;
}

function renderTargetOrbitVerification(summary) {
  const verified = (summary.diagnostics || []).some(
    (diagnostic) => diagnostic.code === "target_orbit_verified",
  );
  elements.verificationSection.hidden = !verified;
  if (!verified) {
    elements.verificationSummary.innerHTML = "";
    return;
  }
  const metrics = new Map(
    (summary.metrics || []).map((metric) => [metric.name, metric.value]),
  );
  elements.verificationSummary.innerHTML = `
    <strong>${escapeHTML(t("targetOrbitVerified"))}</strong>
    <span>${escapeHTML(
      t("targetOrbitEvidence", {
        periapsis: formatNumber(metrics.get("insertion_periapsis_altitude")),
        apoapsis: formatNumber(metrics.get("insertion_apoapsis_altitude")),
        eccentricity: formatNumber(metrics.get("insertion_eccentricity")),
        massError: formatNumber(metrics.get("mass_balance_error")),
      }),
    )}</span>
  `;
}

function localizedHandoverEvent(event) {
  return t(event === "apogee" ? "handoverApogee" : "handoverBurnout");
}

function hideResultSections() {
  for (const key of [
    "plotSection",
    "eventSection",
    "modelSection",
    "artifactSection",
  ]) {
    elements[key].hidden = true;
  }
}

function renderPlots(artifacts) {
  const plots = artifacts.filter(
    (artifact) => artifact.media_type === "image/png",
  );
  elements.plotSection.hidden = plots.length === 0;
  elements.plotGallery.innerHTML = plots
    .map(
      (artifact) => `
        <a
          class="plot-card"
          href="${escapeHTML(artifact.url)}"
          target="_blank"
          rel="noopener"
        >
          <img
            src="${escapeHTML(artifact.url)}"
            alt="${escapeHTML(localizedName("artifacts", artifact.name))}"
            loading="lazy"
          />
          <span>${escapeHTML(artifact.filename)}</span>
        </a>
      `,
    )
    .join("");
}

function renderEvents(events) {
  elements.eventSection.hidden = events.length === 0;
  elements.eventList.innerHTML = events
    .map(
      (event) => `
        <div class="event-item">
          <strong>${escapeHTML(localizedEventName(event.name))}</strong>
          <span>${escapeHTML(
            t("eventTime", { time: formatNumber(event.time_s) }),
          )}</span>
        </div>
      `,
    )
    .join("");
}

function renderModel(manifest) {
  const states = manifest.state_vector || [];
  const equations = manifest.equations || [];
  const limitations = manifest.limitations || [];
  elements.modelSection.hidden =
    states.length === 0 && equations.length === 0;
  elements.modelSummary.textContent = t("modelSummary", {
    states: states.length,
    equations: equations.length,
  });
  const stateChips = states
    .slice(0, 18)
    .map(
      (item) =>
        `<span class="model-chip">${escapeHTML(item.symbol)}</span>`,
    )
    .join("");
  const equationChips = equations
    .map(
      (item) =>
        `<span class="model-chip" title="${escapeHTML(item.id)}">${escapeHTML(
          localizedName("equations", item.id),
        )}</span>`,
    )
    .join("");
  const limitationChips = limitations
    .slice(0, 5)
    .map(
      (item) =>
        `<span class="model-chip">${escapeHTML(localizedLimitation(item))}</span>`,
    )
    .join("");
  elements.modelBody.innerHTML = `
    <div class="model-group">
      <strong>${escapeHTML(t("stateVector"))}</strong>
      <div class="model-chip-list">${stateChips || escapeHTML(t("none"))}</div>
    </div>
    <div class="model-group">
      <strong>${escapeHTML(t("equations"))}</strong>
      <div class="model-chip-list">${equationChips || escapeHTML(t("none"))}</div>
    </div>
    <div class="model-group">
      <strong>${escapeHTML(t("declaredLimitations"))}</strong>
      <div class="model-chip-list">${limitationChips || escapeHTML(t("none"))}</div>
    </div>
  `;
}

function renderArtifacts(artifacts) {
  elements.artifactSection.hidden = artifacts.length === 0;
  elements.artifactList.innerHTML = artifacts
    .map(
      (artifact) => `
        <a
          class="artifact-link"
          href="${escapeHTML(artifact.url)}"
          download="${escapeHTML(artifact.filename)}"
        >
          <span>${escapeHTML(localizedName("artifacts", artifact.name))}</span>
          <small>${escapeHTML(artifact.filename.split(".").pop())}</small>
        </a>
      `,
    )
    .join("");
}

function clearEditor() {
  elements.jsonEditor.value = "";
  elements.parameterForm.innerHTML = emptyMarkup(
    t("noEditableParameters"),
    t("selectTaskSource"),
  );
  elements.editorMode.textContent = t("newTaskMode");
  elements.editorTitle.textContent = t("selectTaskSource");
  setBackendBadgeElement(elements.editorBackend, "");
  elements.saveTask.textContent = t("validateAndAdd");
  setValidationMessage(t("validationDefault"), "");
  renderFamilyVariantControl();
  renderEditorView();
}

function renderQuickStart() {
  const hasDocument = Boolean(elements.jsonEditor.value.trim());
  const hasTasks = state.tasks.length > 0;
  const hasWorkflow = Boolean(state.workflow);
  const workflowComplete =
    hasWorkflow && isTerminalStatus(state.workflow.status);
  const steps = [
    {
      element: elements.quickStartSource,
      status: hasDocument ? "complete" : "active",
    },
    {
      element: elements.quickStartConfigure,
      status: hasTasks ? "complete" : hasDocument ? "active" : "pending",
    },
    {
      element: elements.quickStartQueue,
      status: hasTasks ? "complete" : "pending",
    },
    {
      element: elements.quickStartRun,
      status: workflowComplete
        ? "complete"
        : hasTasks
          ? "active"
          : "pending",
    },
  ];
  for (const step of steps) {
    step.element.classList.toggle("active", step.status === "active");
    step.element.classList.toggle("complete", step.status === "complete");
  }
  const current = workflowComplete || hasWorkflow || hasTasks
    ? 4
    : hasDocument
      ? 2
      : 1;
  elements.quickStartProgress.textContent = t("quickStartProgress", {
    current,
  });
}

function updateControls(forceBusy = false) {
  const busy = forceBusy || isWorkflowActive();
  const assistantAvailable = Boolean(state.assistantStatus?.available);
  const assistantExecuted = Boolean(state.assistantSession?.execution);
  elements.replayWorkflow.disabled = busy || state.tasks.length > 0;
  elements.exportWorkflow.disabled = busy || !state.workflow;
  elements.clearWorkflow.disabled = busy || state.tasks.length === 0;
  elements.runWorkflow.disabled = busy || state.tasks.length === 0;
  elements.validateTask.disabled = busy || !elements.jsonEditor.value.trim();
  elements.saveTask.disabled = busy || !elements.jsonEditor.value.trim();
  elements.importTask.disabled = busy;
  elements.variantSelect.disabled = busy;
  elements.workflowName.disabled = busy;
  elements.assistantPrompt.disabled =
    busy || state.assistantBusy || assistantExecuted;
  elements.newAssistantSession.disabled = busy || state.assistantBusy;
  elements.generateAssistantDraft.disabled =
    busy ||
    state.assistantBusy ||
    assistantExecuted ||
    !assistantAvailable ||
    !elements.assistantPrompt.value.trim();
  elements.applyAssistantDraft.disabled =
    busy ||
    assistantExecuted ||
    state.assistantDraft?.status !== "proposal";
  elements.confirmAndRunAssistantDraft.disabled =
    busy ||
    state.assistantDraft?.status !== "proposal" ||
    !state.assistantSession ||
    Boolean(state.assistantSession?.execution) ||
    state.tasks.length > 0;
  elements.runWorkflow.classList.toggle(
    "next-action",
    !busy && state.tasks.length > 0,
  );
  renderQuickStart();
}

function parseEditorDocument() {
  try {
    const document = JSON.parse(elements.jsonEditor.value);
    if (!document || Array.isArray(document) || typeof document !== "object") {
      throw new Error(t("jsonRootObject"));
    }
    elements.jsonEditor.classList.remove("invalid");
    return document;
  } catch (error) {
    elements.jsonEditor.classList.add("invalid");
    const message = t("jsonParseFailed", { message: error.message });
    setValidationMessage(message, "error");
    toast(message, "error");
    return null;
  }
}

function setValidationMessage(message, tone) {
  elements.validationMessage.className = `validation-message ${tone}`.trim();
  elements.validationMessage.textContent = message;
}

function setBackendBadgeElement(element, backend) {
  element.dataset.backend = backend || "";
  element.className = `backend-badge ${backend ? "" : "neutral"}`.trim();
  element.textContent = backend || t("notSelected");
}

function backendBadge(backend) {
  const label = backend || t("automaticBackend");
  return `
    <span
      class="backend-badge"
      data-backend="${escapeHTML(backend || "")}"
    >${escapeHTML(label)}</span>
  `;
}

function taskLabel(document) {
  const rawLabel = String(
    document.label || document.name || t("unnamedTask"),
  );
  const scenario = scenarioForDocument(document, rawLabel);
  return scenario ? localizedScenarioLabel(scenario) : rawLabel;
}

function taskKind(document) {
  return String(
    document.task_kind || document.dynamics || t("unknownTask"),
  );
}

function taskBackend(document) {
  return String(
    document.backend_preference || document.backend || "",
  );
}

function taskIndex(taskId) {
  return state.tasks.findIndex((item) => item.task_id === taskId);
}

function newTaskId() {
  const timePart = Date.now().toString(36);
  const randomPart = Math.random().toString(36).slice(2, 7);
  return `task-${timePart}-${randomPart}`;
}

function prioritizedMetrics(metrics) {
  const excluded = new Set(["sample_count", "propagation_duration"]);
  const priorities = [
    "insertion_periapsis",
    "insertion_apoapsis",
    "insertion_eccentricity",
    "payload_delivered",
    "mass_balance_error",
    "apogee",
    "maximum_altitude",
    "final_attitude_error",
    "final_angular_rate",
    "impact_speed",
    "maximum_speed",
    "maximum_roll",
    "heading_change",
    "raan_change",
    "energy_drift",
    "maximum_applied",
    "flight_time",
  ];
  const filtered = metrics.filter((metric) => !excluded.has(metric.name));
  filtered.sort((left, right) => {
    const leftIndex = metricPriority(left.name, priorities);
    const rightIndex = metricPriority(right.name, priorities);
    return leftIndex - rightIndex;
  });
  return filtered.slice(0, 12);
}

function metricPriority(name, priorities) {
  const index = priorities.findIndex((token) => name.includes(token));
  return index < 0 ? priorities.length + 1 : index;
}

function formatNumber(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return String(value);
  }
  const absolute = Math.abs(number);
  if ((absolute > 0 && absolute < 0.001) || absolute >= 1_000_000) {
    return number.toExponential(3);
  }
  return new Intl.NumberFormat(
    state.locale === "zh-CN" ? "zh-CN" : "en-US",
    {
      maximumFractionDigits: absolute < 10 ? 5 : 3,
    },
  ).format(number);
}

function humanize(value) {
  return String(value)
    .replaceAll("_", " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function loadInitialLocale() {
  let savedLocale = null;
  try {
    savedLocale = globalThis.localStorage.getItem(LOCALE_STORAGE_KEY);
  } catch {
    savedLocale = null;
  }
  return SUPPORTED_LOCALES.has(savedLocale) ? savedLocale : "zh-CN";
}

async function restoreAssistantSession() {
  let sessionId = null;
  try {
    sessionId = globalThis.sessionStorage.getItem(
      ASSISTANT_SESSION_STORAGE_KEY,
    );
  } catch {
    sessionId = null;
  }
  if (!sessionId) {
    return null;
  }
  try {
    return await api(
      `/api/assistant/sessions/${encodeURIComponent(sessionId)}`,
    );
  } catch {
    rememberAssistantSession(null);
    return null;
  }
}

async function restoreActiveWorkflow() {
  let workflowId = null;
  try {
    workflowId = globalThis.localStorage.getItem(WORKFLOW_STORAGE_KEY);
  } catch {
    workflowId = null;
  }
  if (!workflowId) {
    return null;
  }
  try {
    return await api(`/api/workflows/${encodeURIComponent(workflowId)}`);
  } catch {
    rememberActiveWorkflow(null);
    return null;
  }
}

function rememberActiveWorkflow(workflowId) {
  try {
    if (workflowId) {
      globalThis.localStorage.setItem(WORKFLOW_STORAGE_KEY, workflowId);
    } else {
      globalThis.localStorage.removeItem(WORKFLOW_STORAGE_KEY);
    }
  } catch {
    // Durable history remains available from the server.
  }
}

function rememberAssistantSession(sessionId) {
  try {
    if (sessionId) {
      globalThis.sessionStorage.setItem(
        ASSISTANT_SESSION_STORAGE_KEY,
        sessionId,
      );
    } else {
      globalThis.sessionStorage.removeItem(
        ASSISTANT_SESSION_STORAGE_KEY,
      );
    }
  } catch {
    // Session continuation still works until this page is reloaded.
  }
}

function setLocale(locale) {
  if (!SUPPORTED_LOCALES.has(locale) || locale === state.locale) {
    return;
  }
  state.locale = locale;
  state.assistantSession = null;
  state.assistantDraft = null;
  state.assistantError = null;
  elements.assistantPrompt.value = "";
  rememberAssistantSession(null);
  try {
    globalThis.localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  } catch {
    // Language switching still works when storage is unavailable.
  }
  applyLocale();
}

function applyLocale() {
  document.documentElement.lang = state.locale;
  document.title = t("documentTitle");
  document.getElementById("metaDescription").content = t("metaDescription");

  for (const element of document.querySelectorAll("[data-i18n]")) {
    element.textContent = t(element.dataset.i18n);
  }
  for (const element of document.querySelectorAll(
    "[data-i18n-placeholder]",
  )) {
    element.placeholder = t(element.dataset.i18nPlaceholder);
  }
  for (const element of document.querySelectorAll(
    "[data-i18n-aria-label]",
  )) {
    element.setAttribute("aria-label", t(element.dataset.i18nAriaLabel));
    if (element.hasAttribute("title")) {
      element.setAttribute("title", t(element.dataset.i18nAriaLabel));
    }
  }
  for (const button of elements.languageSwitcher.querySelectorAll(
    "[data-locale]",
  )) {
    const active = button.dataset.locale === state.locale;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  }

  const defaultWorkflowNames = Object.values(translations).map(
    (resource) => resource.strings.workflowNameDefault,
  );
  if (defaultWorkflowNames.includes(elements.workflowName.value)) {
    elements.workflowName.value = t("workflowNameDefault");
  }

  renderServiceStatus();
  renderAssistant();
  renderSourceView();
  renderWorkbenchView();
  if (state.capabilities.length > 0) {
    renderBackendFilters();
  }
  if (state.taskFamilies.length > 0 || state.scenarios.length > 0) {
    renderLibrary();
  }
  renderWorkflowHistory();
  renderQueue();
  refreshEditorLocale();
  renderFamilyVariantControl();
  const currentDocument = parseEditorDocumentSilently();
  if (currentDocument) {
    renderParameterForm(currentDocument);
  }
  renderInspector();
  setValidationMessage(t("validationDefault"), "");
  elements.toastRegion.replaceChildren();
  updateControls();
}

function refreshEditorLocale() {
  if (state.editorMode === "task" && state.selectedTaskId) {
    const task = state.tasks.find(
      (item) => item.task_id === state.selectedTaskId,
    );
    if (task) {
      elements.editorMode.textContent = t("taskMode", {
        number: String(taskIndex(task.task_id) + 1).padStart(2, "0"),
      });
      elements.editorTitle.textContent = taskLabel(task.document);
      setBackendBadgeElement(
        elements.editorBackend,
        taskBackend(task.document),
      );
      elements.saveTask.textContent = t("validateAndUpdate");
      return;
    }
  }

  const scenario = state.scenarios.find(
    (item) => item.scenario_id === state.selectedScenarioId,
  );
  const family = state.taskFamilies.find(
    (item) => item.family_id === state.selectedFamilyId,
  );
  elements.editorMode.textContent = t(
    state.editorMode === "template" ? "templateMode" : "newTaskMode",
  );
  elements.editorTitle.textContent = scenario
    ? localizedScenarioLabel(scenario)
    : family
      ? localizedTaskFamily(family.family_id).label
      : taskLabel(parseEditorDocumentSilently() || {});
  setBackendBadgeElement(
    elements.editorBackend,
    scenario?.backend_id ||
      family?.backend_ids?.[0] ||
      taskBackend(parseEditorDocumentSilently() || {}),
  );
  elements.saveTask.textContent = t("validateAndAdd");
}

function renderServiceStatus() {
  const keys = {
    connecting: "serviceConnecting",
    online: "serviceOnline",
    offline: "serviceOffline",
  };
  elements.serviceStatus.textContent = t(
    keys[state.serviceState] || "serviceConnecting",
  );
}

function t(key, variables = {}) {
  const current = translations[state.locale]?.strings || {};
  const fallback = translations.en.strings;
  let result = current[key] || fallback[key] || key;
  for (const [name, value] of Object.entries(variables)) {
    result = result.replaceAll(`{${name}}`, String(value));
  }
  return result;
}

function statusLabel(status) {
  const key = statusTranslationKeys[status];
  return key ? t(key) : humanize(status);
}

function localizedScenarioLabel(scenario) {
  const translated = translations[state.locale]?.scenarios?.[
    scenario.scenario_id
  ];
  return translated?.label || scenario.label;
}

function localizedScenarioDescription(scenario) {
  const translated = translations[state.locale]?.scenarios?.[
    scenario.scenario_id
  ];
  return translated?.description || scenario.description;
}

function localizedTaskKind(taskKindValue) {
  const translated = translations[state.locale]?.taskKinds?.[taskKindValue];
  const fallback = translations.en.taskKinds?.[taskKindValue];
  return (
    translated ||
    fallback || {
      label: humanize(taskKindValue),
      description: taskKindValue,
    }
  );
}

function localizedTaskVariant(familyId, variant) {
  const translated =
    translations[state.locale]?.taskVariants?.[familyId]?.[
      variant.variant_id
    ];
  const fallback =
    translations.en.taskVariants?.[familyId]?.[variant.variant_id];
  return translated || fallback || localizedTaskKind(variant.task_kind);
}

function localizedTaskFamily(familyId) {
  const translated = translations[state.locale]?.taskFamilies?.[familyId];
  const fallback = translations.en.taskFamilies?.[familyId];
  return (
    translated ||
    fallback || {
      label: humanize(familyId),
      description: familyId,
    }
  );
}

function scenarioForDocument(document, rawLabel) {
  const candidates = new Set(
    [
      document.scenario_id,
      document.request_id,
      document.name,
      document.label,
      rawLabel,
    ]
      .filter(Boolean)
      .map(String),
  );
  return state.scenarios.find(
    (scenario) =>
      candidates.has(scenario.scenario_id) ||
      candidates.has(scenario.label),
  );
}

function requestLabel(request) {
  return taskLabel(request);
}

function localizedName(group, value) {
  const translated = translations[state.locale]?.[group]?.[value];
  return translated || humanize(value);
}

function localizedEventName(name) {
  const translated = translations[state.locale]?.events?.[name];
  if (translated) {
    return translated;
  }
  const controlMatch = name.match(/^control_(.+)_(start|end)$/);
  if (controlMatch) {
    return t(controlMatch[2] === "start" ? "controlStart" : "controlEnd", {
      name: humanize(controlMatch[1]),
    });
  }
  const parachuteMatch = name.match(
    /^parachute_(.+)_(trigger|deployment)$/,
  );
  if (parachuteMatch) {
    return t(
      parachuteMatch[2] === "trigger"
        ? "parachuteTrigger"
        : "parachuteDeployment",
      { name: humanize(parachuteMatch[1]) },
    );
  }
  return humanize(name);
}

function localizedLimitation(value) {
  return translations[state.locale]?.limitations?.[value] || value;
}

function isWorkflowActive() {
  return Boolean(
    state.workflow && !isTerminalStatus(state.workflow.status),
  );
}

function isPlainObject(value) {
  return Boolean(
    value &&
      typeof value === "object" &&
      !Array.isArray(value),
  );
}

function parseEditorDocumentSilently() {
  try {
    const document = JSON.parse(elements.jsonEditor.value);
    return isPlainObject(document) ? document : null;
  } catch {
    return null;
  }
}

function isTerminalStatus(status) {
  return (
    status === "completed" ||
    status === "failed" ||
    status === "interrupted"
  );
}

function emptyMarkup(title, detail) {
  return `
    <div class="empty-state compact">
      <strong>${escapeHTML(title)}</strong>
      <span>${escapeHTML(detail)}</span>
    </div>
  `;
}

function toast(message, tone = "") {
  const item = document.createElement("div");
  item.className = `toast ${tone}`.trim();
  item.textContent = message;
  elements.toastRegion.append(item);
  window.setTimeout(() => item.remove(), 4200);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    const detail =
      typeof payload === "object" && payload?.detail
        ? payload.detail
        : `HTTP ${response.status}`;
    throw new Error(String(detail));
  }
  return payload;
}

function escapeHTML(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
