"use strict";
const TASK_TYPES = [
  ["presentation", "Текст / инструкция"],
  ["guided_speaking", "AI-голос и ответ ребёнка"],
  ["guided_scene", "Разговор на сцене"],
  ["voice_answer", "Голосовой ответ"],
  ["repeat", "Повтори за AI"],
  ["choice", "Выбор ответа (текст / изображение)"],
  ["choice_card", "Выбор ответа"],
  ["card_selector", "Карточки-выбор"],
  ["animal_compare", "Сравнение животных"],
  ["animal_riddle", "Загадка"],
  ["drag_and_drop", "Перетаскивание"],
  ["matching", "Сопоставление"],
  ["drawing", "Рисование"],
  ["tap_select", "Интерактивный тап"],
  ["interactive_scene", "Интерактивная сцена"],
  ["physical_action", "Пауза / физическая активность"],
  ["transition", "Переход"],
  ["roleplay", "Ролевая игра"],
  ["mood_choice", "Выбор настроения"],
  ["personal_travel_story", "Рассказ о путешествии"],
  ["video", "Видео"]
];
const TYPE_LABEL = Object.fromEntries(TASK_TYPES);

const HW_TYPES = [
  ["drawing", "🎨 Рисование"],
  ["voice_answer", "🎤 Голосовой ответ"],
  ["choice", "🔘 Выбор ответа"],
  ["drag_and_drop", "✋ Drag & Drop"],
  ["presentation", "📄 Информационный слайд"],
  ["video", "🎬 Видео"],
  ["repeat", "🔁 Повторение фразы"]
];
const HW_TYPE_LABEL = Object.fromEntries(HW_TYPES);

const OPTION_TYPES = new Set(["choice_card", "card_selector", "mood_choice", "choice"]);
const DRAG_TYPES = new Set(["drag_and_drop", "drag_drop"]);
const RIDDLE_TYPES = new Set(["animal_riddle"]);

const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);
const deepCopy = v => JSON.parse(JSON.stringify(v));
const escH = v => String(v ?? "").replace(/[&<>"']/g, c => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
}[c]));

const state = {
  csrf: "",
  courses: [],
  lessons: [],
  expandedCourses: new Set(),
  expandedLessons: new Set(),
  currentTab: "lesson", // 'lesson' | 'homework'
  lesson: null,
  summary: null,
  lessonId: "",
  homework: null,
  hwDirty: false,
  versions: [],
  media: [],
  dirty: false,
  dialogMode: "slide",
  lessonDialogMode: "create",
  courseDialogMode: "create",
  targetCourseId: null,
  targetLessonIdForMove: null,
  dragIndex: null,
  hwDragIndex: null,
  blobUrls: new Map(),
  courseCoverPreviewUrl: null,
  courseCoverPreviewRequest: 0,
  pendingRoleParentId: null
};

function steps() {
  if (!state.lesson) return [];
  if (Array.isArray(state.lesson.steps)) return state.lesson.steps;
  if (!Array.isArray(state.lesson.slides)) state.lesson.slides = [];
  return state.lesson.slides;
}

function hwSteps() {
  if (!state.homework) return [];
  if (!Array.isArray(state.homework.slides)) state.homework.slides = [];
  return state.homework.slides;
}

function markDirty() {
  state.dirty = true;
  const b = $("#saveButton");
  if (b) b.textContent = "Сохранить урок •";
  clearNotice();
}

function markHwDirty() {
  state.hwDirty = true;
  const b = $("#saveHwBtn");
  if (b) b.textContent = "Сохранить ДЗ •";
  clearNotice();
}

function clearNotice() {
  const n = $("#notice");
  if (n) n.classList.add("hidden");
}

function notice(msg) {
  const n = $("#notice");
  if (!n) return;
  n.textContent = msg;
  n.classList.remove("hidden");
  const ep = $("#errorPanel");
  if (ep) ep.classList.add("hidden");
}

function showErrors(e) {
  const list = Array.isArray(e) ? e : [e?.message || e || "Неизвестная ошибка"];
  const ep = $("#errorPanel");
  if (!ep) { alert(list.join("\n")); return; }
  ep.innerHTML = `<strong>Ошибки:</strong><ul>${list.map(m => `<li>${escH(m)}</li>`).join("")}</ul>`;
  ep.classList.remove("hidden");
  ep.scrollIntoView({ behavior: "smooth", block: "center" });
}

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}), "X-DOME-CSRF": state.csrf };
  if (opts.body && !(opts.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const res = await fetch(path, { ...opts, headers });
  let data = {};
  const responseText = await res.text();
  try { data = JSON.parse(responseText); } catch { data = { error: responseText.slice(0, 240) || `HTTP ${res.status}` }; }
  if (res.status === 401) {
    sessionStorage.removeItem("dome_studio_token");
    state.csrf = "";
    showLogin("Сессия завершена. Войдите снова.");
    throw new Error("Повторный вход");
  }
  if (!res.ok) {
    const err = new Error(data.error || `HTTP ${res.status}`);
    err.details = data.errors || data.technical_errors || [];
    throw err;
  }
  return data;
}

async function fetchBlob(path) {
  if (state.blobUrls.has(path)) return state.blobUrls.get(path);
  if (/^https?:\/\//i.test(path)) return path;
  const enc = encodeURIComponent(path);
  const url = path.startsWith("media/")
    ? `/api/studio/lessons/${state.lessonId}/media/${encodeURIComponent(path.slice(6))}`
    : `/api/studio/lessons/${state.lessonId}/asset?path=${enc}`;
  const res = await fetch(url, { credentials: "same-origin" });
  if (!res.ok) throw new Error(`Файл недоступен: ${path}`);
  const obj = URL.createObjectURL(await res.blob());
  state.blobUrls.set(path, obj);
  return obj;
}

function releaseBlobs() {
  for (const u of state.blobUrls.values()) if (u.startsWith("blob:")) URL.revokeObjectURL(u);
  state.blobUrls.clear();
}

function showLogin(msg = "") {
  if ($("#loginView")) $("#loginView").classList.remove("hidden");
  if ($("#appView")) $("#appView").classList.add("hidden");
  if (msg && $("#loginError")) $("#loginError").textContent = msg;
}

async function login(restore = false) {
  try {
    const auth = restore === true
      ? await api("/api/studio/auth/session")
      : await api("/api/studio/auth/login", {
          method: "POST",
          body: JSON.stringify({
            password: $("#ownerPassword") ? $("#ownerPassword").value.trim() : ""
          })
        });
    state.csrf = auth.csrf;
    if ($("#ownerPassword")) $("#ownerPassword").value = "";
    const status = await api("/api/studio/status");
    if ($("#loginView")) $("#loginView").classList.add("hidden");
    if ($("#appView")) $("#appView").classList.remove("hidden");
    await loadCourses();
    await loadLessons();
    loadDashboard();
  } catch (e) {
    showLogin(restore === true ? "" : (e.message || "Неверный пароль"));
  }
}

async function changeAdminPasswordAction() {
  const current = $("#adminCurrentPassword") ? $("#adminCurrentPassword").value.trim() : "";
  const next = $("#adminNewPassword") ? $("#adminNewPassword").value.trim() : "";
  const confirm = $("#adminNewPasswordConfirm") ? $("#adminNewPasswordConfirm").value.trim() : "";
  const status = $("#changePasswordStatus");
  if (!status) return;
  status.textContent = "";
  if (!current) {
    status.style.color = "#EF4444";
    status.textContent = "Укажите текущий пароль";
    return;
  }
  if (!next || next.length < 6) {
    status.style.color = "#EF4444";
    status.textContent = "Новый пароль должен содержать минимум 6 символов";
    return;
  }
  if (next !== confirm) {
    status.style.color = "#EF4444";
    status.textContent = "Новые пароли не совпадают";
    return;
  }
  try {
    const res = await api("/api/studio/auth/change-password", {
      method: "POST",
      body: JSON.stringify({ old_password: current, new_password: next })
    });
    status.style.color = "#10B981";
    status.textContent = res.message || "Пароль успешно изменён!";
    if ($("#adminCurrentPassword")) $("#adminCurrentPassword").value = "";
    if ($("#adminNewPassword")) $("#adminNewPassword").value = "";
    if ($("#adminNewPasswordConfirm")) $("#adminNewPasswordConfirm").value = "";
  } catch (e) {
    status.style.color = "#EF4444";
    status.textContent = e.message || "Ошибка смены пароля";
  }
}
window.changeAdminPasswordAction = changeAdminPasswordAction;

function switchSection(name) {
  $$(".app-section").forEach(s => s.classList.add("hidden"));
  $$(".nav-btn").forEach(b => b.classList.toggle("active", b.dataset.section === name));
  const el = document.getElementById("section" + name.charAt(0).toUpperCase() + name.slice(1));
  if (el) el.classList.remove("hidden");
  if (name === "promos") loadPromos();
  if (name === "users") loadUsers();
  if (name === "dashboard") loadDashboard();
  if (name === "clients") loadClients();
  if (name === "tariffs") loadTariffs();
}

/* ==========================================================================
   COURSES & HIERARCHY TREE
   ========================================================================== */

async function loadCourses() {
  try {
    const data = await api("/api/studio/courses");
    state.courses = (data.courses || []).map(c => ({ ...c, id: c.course_id || c.id, course_id: c.course_id || c.id }));
    renderCourseSelects();
    renderHierarchyTree();
  } catch (e) {
    console.warn("Failed to load courses:", e);
  }
}

async function loadLessons() {
  try {
    const data = await api("/api/studio/lessons");
    state.lessons = data.lessons || [];
    renderHierarchyTree();
    if (state.lessonId && state.lessons.some(l => l.lesson_id === state.lessonId)) {
      highlightLessonInTree(state.lessonId);
    }
  } catch (e) {
    console.warn("Failed to load lessons:", e);
  }
}

function renderCourseSelects() {
  const selects = [$("#lessonCourseSelect"), $("#newLessonCourseSelect"), $("#targetCourseSelect")].filter(Boolean);
  selects.forEach(sel => {
    const cur = sel.value;
    sel.innerHTML = state.courses.map(c => `<option value="${escH(c.id)}">${escH(c.title || c.id)}</option>`).join("");
    if (cur && state.courses.some(c => c.id === cur)) sel.value = cur;
  });
}

function renderHierarchyTree() {
  const root = $("#hierarchyTree");
  if (!root) return;
  root.innerHTML = "";
  const query = ($("#hierarchySearch")?.value || "").trim().toLowerCase();

  // Group lessons by course
  const courseLessonsMap = new Map();
  state.courses.forEach(c => courseLessonsMap.set(c.id, []));
  const unassigned = [];

  state.lessons.forEach(l => {
    const cid = l.course_id || "conversation";
    if (courseLessonsMap.has(cid)) {
      courseLessonsMap.get(cid).push(l);
    } else {
      unassigned.push(l);
    }
  });

  // Render each course
  state.courses.forEach(course => {
    const lessons = courseLessonsMap.get(course.id) || [];
    const matchesCourse = course.title?.toLowerCase().includes(query) || course.id.toLowerCase().includes(query);
    const matchingLessons = lessons.filter(l => l.title?.toLowerCase().includes(query) || l.lesson_id.toLowerCase().includes(query));

    if (query && !matchesCourse && !matchingLessons.length) return;

    const isExpanded = state.expandedCourses.has(course.id) || Boolean(query);
    const node = document.createElement("div");
    node.className = "tree-node tree-course";

    const header = document.createElement("div");
    header.className = "tree-header";
    header.innerHTML = `
      <span class="tree-toggle">${isExpanded ? "▼" : "▶"}</span>
      <span class="tree-icon">📚</span>
      <span class="tree-label" title="${escH(course.title)}">${escH(course.title || course.id)}</span>
      <span class="tree-badge ${course.status === 'published' ? 'on' : 'off'}">${escH(course.status || 'draft')}</span>
      <span class="tree-badge ${course.active !== false ? 'on' : 'off'}">${course.active !== false ? 'ON' : 'OFF'}</span>
      <div class="tree-actions">
        <button class="tree-action-btn" data-act="course_up" title="Поднять курс">↑</button>
        <button class="tree-action-btn" data-act="course_down" title="Опустить курс">↓</button>
        <button class="tree-action-btn" data-act="add_lesson" title="Создать урок в этом курсе">+ Урок</button>
        <button class="tree-action-btn" data-act="edit_course" title="Настройки курса">✏</button>
        <button class="tree-action-btn" data-act="dup_course" title="Дублировать курс">⧉</button>
        <button class="tree-action-btn" data-act="del_course" title="Архивировать/удалить курс">🗑</button>
      </div>
    `;

    // Toggle expand
    header.addEventListener("click", (ev) => {
      if (ev.target.closest(".tree-actions")) return;
      if (state.expandedCourses.has(course.id)) state.expandedCourses.delete(course.id);
      else state.expandedCourses.add(course.id);
      renderHierarchyTree();
    });

    // Action buttons
    header.querySelector("[data-act=add_lesson]")?.addEventListener("click", (ev) => {
      ev.stopPropagation();
      openLessonDialog("create", course.id);
    });
    header.querySelector("[data-act=course_up]")?.addEventListener("click", (ev) => {
      ev.stopPropagation();
      reorderCourse(course.id, -1);
    });
    header.querySelector("[data-act=course_down]")?.addEventListener("click", (ev) => {
      ev.stopPropagation();
      reorderCourse(course.id, 1);
    });
    header.querySelector("[data-act=edit_course]")?.addEventListener("click", (ev) => {
      ev.stopPropagation();
      openCourseDialog("edit", course);
    });
    header.querySelector("[data-act=dup_course]")?.addEventListener("click", (ev) => {
      ev.stopPropagation();
      duplicateCourse(course.id);
    });
    header.querySelector("[data-act=del_course]")?.addEventListener("click", (ev) => {
      ev.stopPropagation();
      deleteCourse(course.id);
    });

    node.appendChild(header);

    // Lessons container
    if (isExpanded) {
      const children = document.createElement("div");
      children.className = "tree-children";

      const displayLessons = query ? matchingLessons : lessons;
      displayLessons.forEach(lesson => {
        const lessonNode = renderLessonTreeNode(lesson);
        children.appendChild(lessonNode);
      });

      if (!displayLessons.length) {
        const empty = document.createElement("div");
        empty.className = "muted";
        empty.style.padding = "4px 8px";
        empty.style.fontSize = "12px";
        empty.textContent = "Нет уроков в блоке";
        children.appendChild(empty);
      }

      node.appendChild(children);
    }

    root.appendChild(node);
  });

  // Unassigned lessons
  if (unassigned.length) {
    const isExpanded = state.expandedCourses.has("__unassigned__") || Boolean(query);
    const unNode = document.createElement("div");
    unNode.className = "tree-node";
    const unHead = document.createElement("div");
    unHead.className = "tree-header";
    unHead.innerHTML = `
      <span class="tree-toggle">${isExpanded ? "▼" : "▶"}</span>
      <span class="tree-icon">📁</span>
      <span class="tree-label">Другие уроки (${unassigned.length})</span>
    `;
    unHead.addEventListener("click", () => {
      if (state.expandedCourses.has("__unassigned__")) state.expandedCourses.delete("__unassigned__");
      else state.expandedCourses.add("__unassigned__");
      renderHierarchyTree();
    });
    unNode.appendChild(unHead);

    if (isExpanded) {
      const unCh = document.createElement("div");
      unCh.className = "tree-children";
      unassigned.forEach(lesson => unCh.appendChild(renderLessonTreeNode(lesson)));
      unNode.appendChild(unCh);
    }
    root.appendChild(unNode);
  }
}

function renderLessonTreeNode(lesson) {
  const isSelected = lesson.lesson_id === state.lessonId;
  const isExpanded = state.expandedLessons.has(lesson.lesson_id) || isSelected;

  const node = document.createElement("div");
  node.className = "tree-node tree-lesson";

  const head = document.createElement("div");
  head.className = "tree-header" + (isSelected ? " active" : "");
  head.innerHTML = `
    <span class="tree-toggle">${isExpanded ? "▼" : "▶"}</span>
    <span class="tree-icon">🎯</span>
    <span class="tree-label" title="${escH(lesson.title)}">${escH(lesson.title || lesson.lesson_id)}</span>
    <span class="tree-badge ${lesson.status === 'published' ? 'on' : 'off'}">${escH(lesson.status || 'draft')}</span>
    <div class="tree-actions">
      <button class="tree-action-btn" data-act="move_lesson" title="Перенести урок в другой курс">⇄</button>
      <button class="tree-action-btn" data-act="dup_lesson" title="Дублировать урок">⧉</button>
      <button class="tree-action-btn" data-act="del_lesson" title="Удалить урок">🗑</button>
    </div>
  `;

  head.addEventListener("click", (ev) => {
    if (ev.target.closest(".tree-actions")) return;
    openLesson(lesson.lesson_id);
    if (!state.expandedLessons.has(lesson.lesson_id)) {
      state.expandedLessons.add(lesson.lesson_id);
    }
    renderHierarchyTree();
  });

  head.querySelector("[data-act=move_lesson]")?.addEventListener("click", (ev) => {
    ev.stopPropagation();
    openMoveLessonDialog(lesson.lesson_id);
  });
  head.querySelector("[data-act=dup_lesson]")?.addEventListener("click", (ev) => {
    ev.stopPropagation();
    openLessonDialog("duplicate", lesson.course_id, lesson);
  });
  head.querySelector("[data-act=del_lesson]")?.addEventListener("click", (ev) => {
    ev.stopPropagation();
    deleteLesson(lesson.lesson_id);
  });

  node.appendChild(head);

  if (isExpanded) {
    const sub = document.createElement("div");
    sub.className = "tree-children";

    // 1. Lesson Steps item
    const stepsItem = document.createElement("div");
    stepsItem.className = "tree-item-hw" + (isSelected && state.currentTab === "lesson" ? " active" : "");
    stepsItem.innerHTML = `<span class="tree-icon">📄</span> <span>Шаги урока (${lesson.step_count || lesson.slide_count || '...'})</span>`;
    stepsItem.addEventListener("click", () => {
      openLesson(lesson.lesson_id).then(() => switchEditorTab("lesson"));
    });
    sub.appendChild(stepsItem);

    // 2. Homework item
    const hwItem = document.createElement("div");
    hwItem.className = "tree-item-hw" + (isSelected && state.currentTab === "homework" ? " active" : "");
    const hwOn = lesson.has_homework !== false;
    hwItem.innerHTML = `
      <span class="tree-icon">📝</span>
      <span class="tree-label">Домашнее задание</span>
      <span class="tree-badge ${hwOn ? 'on' : 'off'}">${hwOn ? 'ON' : 'OFF'}</span>
    `;
    hwItem.addEventListener("click", () => {
      openLesson(lesson.lesson_id).then(() => switchEditorTab("homework"));
    });
    sub.appendChild(hwItem);

    node.appendChild(sub);
  }

  return node;
}

function highlightLessonInTree(id) {
  $$(".tree-header").forEach(h => h.classList.remove("active"));
  renderHierarchyTree();
}

/* ==========================================================================
   COURSE CRUD
   ========================================================================== */

function openCourseDialog(mode, course = null) {
  state.courseDialogMode = mode;
  state.targetCourseId = course ? course.id : null;
  const isEdit = mode === "edit";

  if ($("#courseDialogTitle")) $("#courseDialogTitle").textContent = isEdit ? "Редактировать курс/блок" : "Новый курс/блок";
  if ($("#confirmCourseAction")) $("#confirmCourseAction").textContent = isEdit ? "Сохранить изменения" : "Создать курс";

  const idInput = $("#courseIdInput");
  if (idInput) {
    idInput.value = course ? course.id : "";
    idInput.disabled = isEdit;
  }
  if ($("#courseTitleInput")) $("#courseTitleInput").value = course ? course.title : "";
  if ($("#courseDescInput")) $("#courseDescInput").value = course ? (course.description || "") : "";
  if ($("#courseOrderInput")) $("#courseOrderInput").value = course ? (course.order || 1) : state.courses.length + 1;
  if ($("#courseStatusInput")) $("#courseStatusInput").value = course ? (course.status || "published") : "published";
  if ($("#courseActiveInput")) $("#courseActiveInput").checked = course ? course.active !== false : true;
  if ($("#courseLockedInput")) $("#courseLockedInput").checked = course ? Boolean(course.locked) : false;
  const coverInput = $("#courseCoverFile");
  if (coverInput) coverInput.value = "";
  renderCourseCoverPreview(course);

  if ($("#courseDialog")) $("#courseDialog").showModal();
}

function replaceCourseCoverPreview(node) {
  const host = $("#courseCoverPreview");
  if (!host) return;
  if (state.courseCoverPreviewUrl) URL.revokeObjectURL(state.courseCoverPreviewUrl);
  state.courseCoverPreviewUrl = null;
  host.replaceChildren(node);
}

function courseCoverImage(url, label) {
  const wrapper = document.createElement("div");
  const image = document.createElement("img");
  image.src = url;
  image.alt = "Обложка курса";
  image.style.cssText = "display:block;max-width:180px;max-height:96px;object-fit:contain;border-radius:8px;margin:6px 0";
  const caption = document.createElement("span");
  caption.textContent = label;
  wrapper.append(image, caption);
  return wrapper;
}

function renderCourseCoverPreview(course) {
  const host = $("#courseCoverPreview");
  if (!host) return;
  const requestId = ++state.courseCoverPreviewRequest;
  if (!course?.cover_image) {
    replaceCourseCoverPreview(document.createTextNode("Обложка не выбрана"));
    return;
  }
  replaceCourseCoverPreview(document.createTextNode("Загружаем текущую обложку…"));
  fetch(course.cover_image, { credentials: "same-origin" })
    .then(res => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.blob();
    })
    .then(blob => {
      const url = URL.createObjectURL(blob);
      if (requestId !== state.courseCoverPreviewRequest) {
        URL.revokeObjectURL(url);
        return;
      }
      state.courseCoverPreviewUrl = url;
      const preview = $("#courseCoverPreview");
      if (preview) {
        preview.replaceChildren(courseCoverImage(state.courseCoverPreviewUrl, "Текущая обложка. Выберите файл, чтобы заменить её."));
      }
    })
    .catch(() => {
      if (requestId !== state.courseCoverPreviewRequest) return;
      const preview = $("#courseCoverPreview");
      if (preview) preview.textContent = "Текущая обложка сохранена, но предпросмотр временно недоступен.";
    });
}

function slugifyCourseTitle(title) {
  const ruMap = {'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'e','ж':'zh','з':'z','и':'i','й':'y','к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'h','ц':'ts','ч':'ch','ш':'sh','щ':'sch','ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya'};
  let s = String(title || '').toLowerCase().trim();
  let res = '';
  for (const ch of s) {
    if (ruMap[ch] !== undefined) res += ruMap[ch];
    else if (/[a-z0-9]/.test(ch)) res += ch;
    else if (ch === ' ' || ch === '-' || ch === '_') res += '_';
  }
  res = res.replace(/_+/g, '_').replace(/^_+|_+$/g, '');
  return res || 'course_' + Date.now().toString(36);
}

async function confirmCourseAction(ev) {
  ev.preventDefault();
  let id = ($("#courseIdInput")?.value || "").trim().toLowerCase();
  const title = ($("#courseTitleInput")?.value || "").trim();
  const description = ($("#courseDescInput")?.value || "").trim();
  const order = Number($("#courseOrderInput")?.value) || 1;
  const status = $("#courseStatusInput")?.value || "published";
  const active = $("#courseActiveInput")?.checked !== false;
  const locked = $("#courseLockedInput")?.checked === true;

  if (!title) { showErrors("Название курса обязательно."); return; }
  if (!id) {
    id = slugifyCourseTitle(title);
  }

  const payload = { course_id: id, id, title, description, order, status, active, locked };
  try {
    let saved;
    if (state.courseDialogMode === "edit") {
      saved = await api(`/api/studio/courses/${state.targetCourseId}`, { method: "PUT", body: JSON.stringify(payload) });
      notice("Курс обновлен.");
    } else {
      saved = await api("/api/studio/courses", { method: "POST", body: JSON.stringify(payload) });
      state.expandedCourses.add(id);
      notice("Курс создан.");
    }
    const file = $("#courseCoverFile")?.files?.[0];
    if (file) {
      const form = new FormData();
      form.append("file", file);
      await api(`/api/studio/courses/${saved.course?.course_id || id}/cover`, { method: "POST", body: form });
      notice("Курс и обложка сохранены.");
    }
    if ($("#courseDialog")) $("#courseDialog").close();
    await loadCourses();
  } catch (e) {
    showErrors(e.details?.length ? e.details : e.message);
  }
}

async function duplicateCourse(courseId) {
  if (!confirm(`Создать копию курса «${courseId}»?`)) return;
  try {
    const data = await api(`/api/studio/courses/${courseId}/duplicate`, { method: "POST" });
    notice(`Курс скопирован: ${data.course?.id}`);
    await loadCourses();
    await loadLessons();
  } catch (e) {
    showErrors(e.details?.length ? e.details : e.message);
  }
}

async function deleteCourse(courseId) {
  if (!confirm(`Удалить/архивировать курс «${courseId}»? (При наличии уроков он будет безопасно переведён в архив)`)) return;
  try {
    const data = await api(`/api/studio/courses/${courseId}`, { method: "DELETE" });
    notice(data.archived ? "Курс безопасно архивирован." : "Курс удален.");
    await loadCourses();
    await loadLessons();
  } catch (e) {
    showErrors(e.details?.length ? e.details : e.message);
  }
}

async function reorderCourse(courseId, direction) {
  const ordered = state.courses.map(course => course.id);
  const index = ordered.indexOf(courseId);
  const target = index + direction;
  if (index < 0 || target < 0 || target >= ordered.length) return;
  [ordered[index], ordered[target]] = [ordered[target], ordered[index]];
  try {
    await api("/api/studio/courses/reorder", { method: "POST", body: JSON.stringify({ order: ordered }) });
    await loadCourses();
    notice("Порядок курсов сохранён.");
  } catch (e) {
    showErrors(e.details?.length ? e.details : e.message);
  }
}

function openMoveLessonDialog(lessonId) {
  state.targetLessonIdForMove = lessonId;
  const lesson = state.lessons.find(l => l.lesson_id === lessonId);
  if ($("#moveLessonNotice")) {
    $("#moveLessonNotice").textContent = `Урок «${lesson?.title || lessonId}» (текущий курс: ${lesson?.course_id || 'conversation'})`;
  }
  const sel = $("#targetCourseSelect");
  if (sel) {
    sel.innerHTML = state.courses.map(c => `<option value="${escH(c.id)}">${escH(c.title || c.id)}</option>`).join("");
    if (lesson?.course_id) sel.value = lesson.course_id;
  }
  if ($("#moveLessonDialog")) $("#moveLessonDialog").showModal();
}

async function confirmMoveLesson(ev) {
  ev.preventDefault();
  const targetCourseId = $("#targetCourseSelect")?.value;
  if (!targetCourseId || !state.targetLessonIdForMove) return;
  try {
    await api(`/api/studio/lessons/${state.targetLessonIdForMove}/move`, {
      method: "POST",
      body: JSON.stringify({ target_course_id: targetCourseId })
    });
    if ($("#moveLessonDialog")) $("#moveLessonDialog").close();
    notice("Урок перенесён в черновик. Проверьте и опубликуйте его — активные сессии детей не изменятся.");
    await loadCourses();
    await loadLessons();
    if (state.lessonId === state.targetLessonIdForMove) {
      await openLesson(state.lessonId);
    }
  } catch (e) {
    showErrors(e.details?.length ? e.details : e.message);
  }
}

/* ==========================================================================
   EDITOR TABS: LESSON vs HOMEWORK
   ========================================================================== */

function switchEditorTab(tab) {
  state.currentTab = tab;
  const isLesson = tab === "lesson";
  $("#tabLessonBtn")?.classList.toggle("active", isLesson);
  $("#tabHomeworkBtn")?.classList.toggle("active", !isLesson);
  $("#paneLesson")?.classList.toggle("hidden", !isLesson);
  $("#paneHomework")?.classList.toggle("hidden", isLesson);

  if (!isLesson && state.lessonId) {
    loadHomework(state.lessonId);
  }
}

/* ==========================================================================
   LESSON EDITOR
   ========================================================================== */

async function openLesson(id) {
  if ((state.dirty || state.hwDirty) && !confirm("Есть несохранённые изменения. Открыть другой урок?")) return;
  try {
    releaseBlobs();
    const data = await api(`/api/studio/lessons/${id}`);
    state.lesson = deepCopy(data.lesson);
    state.summary = data.summary || {};
    state.lessonId = id;
    state.versions = data.versions || [];
    state.media = data.media || [];
    state.dirty = false;
    state.hwDirty = false;

    if ($("#emptyState")) $("#emptyState").classList.add("hidden");
    if ($("#editorBody")) $("#editorBody").classList.remove("hidden");
    if ($("#tabLessonTitle")) $("#tabLessonTitle").textContent = state.lesson.title || id;

    if ($("#lessonTitle")) $("#lessonTitle").value = state.lesson.title || id;
    if ($("#lessonIdLabel")) $("#lessonIdLabel").textContent = id;
    const status = data.summary?.status || state.lesson.status || "draft";
    if ($("#lessonMeta")) $("#lessonMeta").textContent = `Курс: ${state.lesson.course_id || "conversation"} · Статус: ${status} · Ревизия: ${state.lesson.revision || 1}`;

    setV("lessonTargetLanguage", state.lesson.target_language || "ru");
    setV("lessonExplanationLanguage", state.lesson.explanation_language || state.lesson.native_language || "ru");
    setV("lessonDifficulty", state.lesson.difficulty || "PRE_A1");
    setV("lessonMaxRuns", state.lesson.max_completed_runs || state.lesson.max_runs || 2);
    setV("lessonAccessMode", state.lesson.access_mode || "free");
    setV("lessonMinAge", state.lesson.min_age || "");
    setV("lessonCourseSelect", state.lesson.course_id || "conversation");
    setV("lessonDescription", state.lesson.description || "");

    if ($("#toggleLessonButton")) $("#toggleLessonButton").textContent = state.summary.published ? "Отключить" : "Включить";
    if ($("#deleteLessonButton")) $("#deleteLessonButton").disabled = state.summary.source !== "persistent";
    if ($("#saveButton")) $("#saveButton").textContent = "Сохранить урок";
    if ($("#errorPanel")) $("#errorPanel").classList.add("hidden");

    clearNotice();
    renumber();
    renderSteps();
    renderHierarchyTree();

    // Automatically load homework metadata for tab badge
    loadHomework(id, false);

  } catch (e) {
    showErrors(e.details?.length ? e.details : e.message);
  }
}

function setV(id, val) {
  const el = document.getElementById(id);
  if (el) el.value = String(val);
}

function renumber() {
  steps().forEach((s, i) => {
    s.order = i + 1;
    if (!s.slide_id) s.slide_id = uniqueId(s.type === "video" ? "video" : "step");
  });
}

function uniqueId(pfx) {
  const used = new Set(steps().map(s => String(s.slide_id || s.id)));
  let i = steps().length + 1, v;
  do { v = `${pfx}_${String(i++).padStart(2, "0")}`; } while (used.has(v));
  return v;
}

function sourceOf(step) {
  const m = Array.isArray(step.media_sequence)
    ? step.media_sequence.find(x => x && ["image", "video", "animation"].includes(String(x.type || "").toLowerCase()))
    : null;
  return String(m?.src || step.src || step.video_file || step.video_url || step.image || step.image_file || "");
}

function isVideo(step) {
  return String(step.type || "").toLowerCase() === "video" || /\.(mp4|mov|m4v|webm)(\?|$)/i.test(sourceOf(step));
}

function setMedia(step, path, video) {
  if (video) {
    step.type = "video";
    step.src = path;
    step.video_file = path;
    delete step.video_url;
    step.media_sequence = [{
      id: "video", type: "video", src: path,
      autoplay: step.autoplay !== false, auto_continue: step.auto_continue !== false,
      skippable: step.skippable !== false, replay: step.replay !== false
    }];
  } else {
    step.image = path;
    step.image_file = path;
    step.src = path;
    const ex = Array.isArray(step.media_sequence)
      ? step.media_sequence.filter(x => !x || !["image", "animation"].includes(String(x.type || "").toLowerCase()))
      : [];
    step.media_sequence = [{ id: "visual", type: "image", src: path }, ...ex];
  }
}

function typeOpts(sel) {
  return TASK_TYPES.map(([v, l]) => `<option value="${v}"${v === sel ? " selected" : ""}>${escH(l)}</option>`).join("") +
    (TYPE_LABEL[sel] ? "" : `<option value="${escH(sel)}" selected>${escH(sel || "Другой")}</option>`);
}

function valFrom(step, key, legacy = []) {
  const d = String(step[key] || "").trim();
  if (d) return d;
  for (const k of legacy) {
    const v = String(step[k] || "").trim();
    if (v) return v;
  }
  return "";
}

function listText(arr) {
  return (Array.isArray(arr) ? arr : []).map(i => typeof i === "string" ? i : (i?.label_ru || i?.label_en || i?.label || i?.text || i?.id || "")).filter(Boolean).join("\n");
}

function answerV(step) {
  const m = String(step.answer_mode || "");
  if (m === "none") return "none";
  if (m.includes("required") || step.requiredForMovie) return "required";
  if (m.includes("optional")) return "optional";
  return "none";
}

function riddleTxt(step) {
  if (!Array.isArray(step.riddle_options)) return "";
  return step.riddle_options.map(o => `${o.id || ""}=${o.label || o.label_en || o.text || ""}`).join("\n");
}

function vmVal(step, key) {
  const vm = step.visual_metadata;
  if (!vm || typeof vm !== "object") return "";
  return String(vm[key] || "");
}

function renderSteps() {
  const host = $("#steps");
  if (!host) return;
  host.innerHTML = "";
  renumber();
  steps().forEach((step, i) => host.append(renderStep(step, i)));
  if (!steps().length) host.innerHTML = '<div class="empty card"><h3>В уроке пока нет шагов</h3><p>Добавьте первый слайд или видео.</p></div>';
  renderMediaLibrary();
}

function mediaKind(asset) {
  const path = String(asset.path || asset.name || "").toLowerCase();
  if (/\.(mp4|mov|m4v|webm)$/.test(path)) return "video";
  if (/\.(mp3|m4a|wav|aac|ogg)$/.test(path)) return "audio";
  return "image";
}

function renderMediaLibrary() {
  const host = $("#mediaLibrary");
  if (!host) return;
  const query = ($("#mediaSearch")?.value || "").trim().toLowerCase();
  const filter = $("#mediaFilter")?.value || "";
  const assets = (state.media || []).filter(asset => {
    const kind = mediaKind(asset);
    const text = `${asset.display_name || ""} ${asset.name || ""}`.toLowerCase();
    return (!query || text.includes(query)) && (!filter || kind === filter);
  });
  host.innerHTML = "";
  if (!assets.length) {
    host.innerHTML = '<div class="muted">Подходящих файлов пока нет. Загрузите файл в любом шаге — он появится здесь.</div>';
    return;
  }
  for (const asset of assets) {
    const row = document.createElement("article");
    row.className = "step-card";
    row.innerHTML = `<div class="step-summary"><span class="step-badge">${escH(mediaKind(asset))}</span><strong class="step-name">${escH(asset.display_name || asset.name)}</strong><span class="muted">${Math.max(1, Math.round((asset.size || 0) / 1024))} KB</span><button type="button" class="insert-media">Вставить шагом</button><button type="button" class="remove-media danger">Удалить</button></div><div class="media-preview"></div>`;
    row.querySelector(".insert-media")?.addEventListener("click", () => {
      const video = mediaKind(asset) === "video";
      const step = {
        slide_id: uniqueId(video ? "video" : "slide"),
        order: steps().length + 1,
        type: video ? "video" : "presentation",
        prompt: video ? "Посмотри видео" : "Рассмотри изображение",
        requiredForMovie: false,
      };
      setMedia(step, asset.path, video);
      steps().push(step);
      markDirty();
      renderSteps();
      notice("Файл добавлен новым шагом. Сохраните урок.");
    });
    row.querySelector(".remove-media")?.addEventListener("click", () => removeMediaAsset(asset));
    host.append(row);
    renderMedia(row.querySelector(".media-preview"), asset.path, mediaKind(asset) === "video");
  }
}

async function removeMediaAsset(asset) {
  if (!confirm(`Удалить файл «${asset.display_name || asset.name}»? Он будет удалён только если не используется в уроке.`)) return;
  try {
    await api(`/api/studio/lessons/${state.lessonId}/media/${encodeURIComponent(asset.name)}`, { method: "DELETE" });
    state.media = state.media.filter(item => item.name !== asset.name);
    releaseBlobs();
    renderSteps();
    notice("Неиспользуемый файл удалён.");
  } catch (error) {
    showErrors(error.details?.length ? error.details : error.message);
  }
}

function renderStep(step, index) {
  const tpl = document.getElementById("stepTemplate").content.firstElementChild.cloneNode(true);
  tpl.dataset.index = String(index);
  const video = isVideo(step), type = String(step.type || "presentation");
  tpl.querySelector(".step-number").textContent = String(index + 1);
  const badge = tpl.querySelector(".step-badge");
  badge.textContent = video ? "Видео" : (TYPE_LABEL[type] || type);
  badge.className = "step-badge" + (video ? " video" : "");
  tpl.querySelector(".step-name").textContent = valFrom(step, "bot_says_target", ["question", "task_goal", "prompt"]) || step.slide_id || `Шаг ${index + 1}`;

  const dot = tpl.querySelector(".enabled-dot");
  if (dot) {
    dot.style.color = step.disabled ? "#E2E8F0" : "#13A864";
    dot.style.cursor = "pointer";
    dot.style.fontSize = "18px";
    dot.title = step.disabled ? "Выключен — нажмите чтобы включить" : "Включён — нажмите чтобы выключить";
    if (step.disabled) tpl.style.opacity = "0.55";
    dot.addEventListener("click", () => {
      step.disabled = !step.disabled;
      markDirty();
      renderSteps();
    });
  }

  const sId = tpl.querySelector("[data-field=slide_id]"); if (sId) sId.value = step.slide_id || "";
  const sType = tpl.querySelector("[data-field=type]"); if (sType) sType.innerHTML = typeOpts(type);
  const bst = tpl.querySelector("[data-field=bot_says_target]"); if (bst) bst.value = valFrom(step, "bot_says_target", ["question", "prompt"]);
  const bsn = tpl.querySelector("[data-field=bot_says_native]"); if (bsn) bsn.value = valFrom(step, "bot_says_native", ["bot_explains_native", "native_hint", "native_explanation"]);
  const ain = tpl.querySelector("[data-field=ai_instruction]"); if (ain) ain.value = valFrom(step, "ai_instruction", ["tutor_instruction"]);
  const pg = tpl.querySelector("[data-field=pedagogical_goal]"); if (pg) pg.value = valFrom(step, "pedagogical_goal", ["task_goal", "target_meaning"]);

  ["label", "color_hint", "fact_hint", "image_key"].forEach(k => {
    const el = tpl.querySelector(`[data-vm="${k}"]`);
    if (el) el.value = vmVal(step, k);
  });

  const me = tpl.querySelector("[data-list=model_examples]"); if (me) me.value = listText(step.model_examples || step.target_language_options);
  const oe = tpl.querySelector("[data-list=options]"); if (oe) oe.value = listText(step.selection_options || step.options);
  const die = tpl.querySelector("[data-list=drag_items]"); if (die) die.value = listText(step.drag_items || step.items);
  const dte = tpl.querySelector("[data-list=drag_targets]"); if (dte) dte.value = listText(step.drag_targets || step.targets);

  const rd = tpl.querySelector(".riddle-fields");
  if (rd) {
    if (RIDDLE_TYPES.has(type)) {
      rd.classList.remove("hidden");
      const rr = tpl.querySelector("[data-field=riddle_options_raw]"); if (rr) rr.value = riddleTxt(step);
      const ci = tpl.querySelector("[data-field=correct_choice_id]"); if (ci) ci.value = step.correct_choice_id || "";
    } else rd.classList.add("hidden");
  }

  const ac = tpl.querySelector("[data-control=answer]"); if (ac) ac.value = answerV(step);
  const cc = tpl.querySelector("[data-control=continue]"); if (cc) cc.value = step.continue_policy || "always";
  const hc = tpl.querySelector("[data-control=hint]"); if (hc) hc.checked = step.hint_enabled !== false;
  const fc = tpl.querySelector("[data-control=follow]"); if (fc) fc.checked = step.allow_ai_followup === true || step.follow_up_policy === "optional";
  const sk = tpl.querySelector("[data-direct_bool=allow_skip]"); if (sk) sk.checked = step.allow_skip === true;
  const at = tpl.querySelector("[data-direct=max_attempts]"); if (at) at.value = step.max_attempts || 3;
  const oq = tpl.querySelector("[data-config=open_question_first]"); if (oq) oq.checked = step.open_question_first !== false;
  const ea = tpl.querySelector("[data-config=examples_allowed]"); if (ea) ea.checked = step.examples_allowed !== false;
  const as = tpl.querySelector("[data-config=adaptive_scaffolding]"); if (as) as.checked = step.adaptive_scaffolding !== false && step.adaptive !== false;
  const rm = tpl.querySelector("[data-config=requiredForMovie]"); if (rm) rm.checked = step.requiredForMovie === true;

  const to = tpl.querySelector(".task-options"); if (to) to.classList.toggle("hidden", !OPTION_TYPES.has(type));
  tpl.querySelectorAll(".drag-items").forEach(el => el.classList.toggle("hidden", !DRAG_TYPES.has(type)));
  const vo = tpl.querySelector(".video-options"); if (vo) vo.classList.toggle("hidden", !video);
  const va = tpl.querySelector("[data-video=autoplay]"); if (va) va.checked = step.autoplay !== false;
  const vs = tpl.querySelector("[data-video=skippable]"); if (vs) vs.checked = step.skippable !== false;
  const vr = tpl.querySelector("[data-video=replay]"); if (vr) vr.checked = step.replay !== false;
  const vac = tpl.querySelector("[data-video=auto_continue]"); if (vac) vac.checked = step.auto_continue !== false && step.autoContinue !== false;

  const src = sourceOf(step);
  const mp = tpl.querySelector(".media-path"); if (mp) mp.textContent = src || "Медиафайл не выбран";
  const repM = tpl.querySelector(".replace-media");
  if (repM) repM.accept = video ? "video/mp4,video/quicktime,video/webm" : "image/*,video/mp4,video/quicktime";
  const existingMedia = tpl.querySelector(".existing-media");
  if (existingMedia) {
    const options = (state.media || []).map(asset => {
      const label = `${asset.display_name || asset.name} (${Math.max(1, Math.round((asset.size || 0) / 1024))} KB)`;
      return `<option value="${escH(asset.path)}">${escH(label)}</option>`;
    }).join("");
    existingMedia.innerHTML = `<option value="">— выбрать уже загруженный файл —</option>${options}`;
    existingMedia.value = (state.media || []).some(asset => asset.path === src) ? src : "";
    existingMedia.addEventListener("change", event => {
      const path = event.target.value;
      if (!path) return;
      setMedia(step, path, /\.(mp4|mov|m4v|webm)(\?|$)/i.test(path));
      markDirty();
      renderSteps();
    });
  }
  renderMedia(tpl.querySelector(".media-preview"), src, video);

  const advEl = tpl.querySelector("[data-adv]");
  const ADV = ["visual_metadata", "scene_script", "content_boxes", "card_options", "selection_options", "card_question_sets", "personal_travel_followups", "hero_placement", "hero_anchor", "character_box", "hero_box", "conversation_goal", "mood_options", "drag_source_asset", "drag_target_asset", "pre_slide_video", "required_phrase_id", "riddle_options", "simplified_text", "diagnostic", "pair", "post_required_phrase_id"];
  const advData = {}; ADV.forEach(k => { if (step[k] !== undefined) advData[k] = step[k]; });
  if (advEl) advEl.value = JSON.stringify(advData, null, 2);

  const applyBtn = tpl.querySelector(".apply-advanced-btn");
  if (applyBtn) applyBtn.addEventListener("click", () => {
    try {
      const parsed = JSON.parse(advEl.value);
      Object.assign(steps()[index], parsed);
      markDirty();
      renderSteps();
      notice("Advanced JSON применён.");
    } catch (ex) { alert("Ошибка JSON: " + ex.message); }
  });

  tpl.querySelectorAll("[data-field]").forEach(el => el.addEventListener("input", ev => updateField(index, ev.target.dataset.field, ev.target.value)));
  tpl.querySelectorAll("[data-direct]").forEach(el => el.addEventListener("input", ev => { steps()[index][ev.target.dataset.direct] = Number(ev.target.value) || 3; markDirty(); }));
  tpl.querySelectorAll("[data-direct_bool]").forEach(el => el.addEventListener("change", ev => { steps()[index][ev.target.dataset.direct_bool] = ev.target.checked; markDirty(); }));
  tpl.querySelectorAll("[data-vm]").forEach(el => el.addEventListener("input", ev => updateVm(index, ev.target.dataset.vm, ev.target.value)));
  tpl.querySelectorAll("[data-control]").forEach(el => el.addEventListener("change", ev => updateControl(index, ev.target.dataset.control, el.type === "checkbox" ? el.checked : el.value)));
  tpl.querySelectorAll("[data-config]").forEach(el => el.addEventListener("change", ev => updateConfig(index, ev.target.dataset.config, ev.target.checked)));
  tpl.querySelectorAll("[data-list]").forEach(el => el.addEventListener("change", ev => updateList(index, ev.target.dataset.list, ev.target.value)));
  tpl.querySelectorAll("[data-video]").forEach(el => el.addEventListener("change", ev => updateVideo(index, ev.target.dataset.video, ev.target.checked)));
  if (repM) repM.addEventListener("change", ev => replaceStepMedia(index, ev.target.files?.[0]));

  const mu = tpl.querySelector(".move-up"); if (mu) mu.addEventListener("click", () => moveStep(index, index - 1));
  const md = tpl.querySelector(".move-down"); if (md) md.addEventListener("click", () => moveStep(index, index + 1));
  const dup = tpl.querySelector(".duplicate-step"); if (dup) dup.addEventListener("click", () => duplicateStep(index));
  const del = tpl.querySelector(".delete-step"); if (del) del.addEventListener("click", () => deleteStep(index));
  const exp = tpl.querySelector(".toggle-expand");
  if (exp) exp.addEventListener("click", () => {
    const body = tpl.querySelector(".step-body");
    const col = body.style.display === "none";
    body.style.display = col ? "" : "none";
    exp.textContent = col ? "▼" : "▶";
  });

  tpl.setAttribute("draggable", "true");
  tpl.addEventListener("dragstart", ev => { state.dragIndex = index; tpl.classList.add("dragging"); ev.dataTransfer.effectAllowed = "move"; ev.dataTransfer.setData("text/dome", String(index)); });
  tpl.addEventListener("dragend", () => { state.dragIndex = null; $$(".step-card").forEach(c => c.classList.remove("dragging", "drop-before")); });
  tpl.addEventListener("dragover", ev => { ev.preventDefault(); tpl.classList.add("drop-before"); });
  tpl.addEventListener("dragleave", () => tpl.classList.remove("drop-before"));
  tpl.addEventListener("drop", ev => {
    ev.preventDefault();
    tpl.classList.remove("drop-before");
    const from = Number(ev.dataTransfer.getData("text/dome"));
    if (!isNaN(from) && from !== index) moveStep(from, index);
  });

  return tpl;
}

async function renderMedia(host, src, video) {
  if (!host) return;
  host.textContent = src ? "Загружаем..." : (video ? "Выберите видео" : "Нет изображения");
  if (!src) return;
  try {
    const url = await fetchBlob(src);
    host.innerHTML = "";
    const el = document.createElement(video ? "video" : "img");
    el.src = url;
    if (video) { el.controls = true; el.muted = true; }
    el.alt = "Предпросмотр";
    host.append(el);
  } catch (e) { host.textContent = e.message; }
}

function updateField(i, key, value) {
  const step = steps()[i];
  step[key] = value;
  if (key === "bot_says_target") { step.question = value; step.task_goal = value; step.prompt = value; }
  if (key === "pedagogical_goal") { step.task_goal = value; step.target_meaning = value; }
  if (key === "ai_instruction") step.tutor_instruction = value;
  if (key === "bot_says_native") { step.bot_explains_native = value; step.native_hint = value; step.native_explanation = value; }
  if (key === "riddle_options_raw") parseRiddle(i, value);
  if (key === "type") renderSteps();
  else {
    const card = document.querySelector(`.step-card[data-index="${i}"]`);
    if (card) card.querySelector(".step-name").textContent = valFrom(step, "bot_says_target", ["question", "task_goal", "prompt"]) || step.slide_id;
  }
  markDirty();
}

function parseRiddle(i, raw) {
  const step = steps()[i];
  step.riddle_options = raw.split(/\n/).map(line => {
    const idx = line.indexOf("=");
    if (idx < 0) return null;
    const id = line.slice(0, idx).trim(), label = line.slice(idx + 1).trim();
    if (!id || !label) return null;
    return { id, label, label_en: label };
  }).filter(Boolean);
}

function updateVm(i, key, value) {
  const step = steps()[i];
  if (!step.visual_metadata || typeof step.visual_metadata !== "object") step.visual_metadata = {};
  if (value) step.visual_metadata[key] = value;
  else delete step.visual_metadata[key];
  markDirty();
}

function updateControl(i, key, value) {
  const step = steps()[i];
  step.controls = step.controls && typeof step.controls === "object" ? step.controls : {};
  if (key === "answer") {
    const en = value !== "none";
    step.controls.answer = { enabled: en, required: value === "required" };
    step.answer_mode = !en ? "none" : value === "required" ? "required_voice" : "optional_voice";
  }
  if (key === "continue") { step.controls.continue = { enabled: true, when: value }; step.continue_policy = value; }
  if (key === "hint") { step.controls.hint = { enabled: Boolean(value) }; step.hint_enabled = Boolean(value); }
  if (key === "follow") { step.controls.follow_up = { enabled: Boolean(value) }; step.follow_up_policy = value ? "optional" : "none"; step.allow_ai_followup = Boolean(value); }
  markDirty();
}

function updateConfig(i, key, value) {
  const step = steps()[i];
  step[key] = Boolean(value);
  if (key === "adaptive_scaffolding") step.adaptive = Boolean(value);
  markDirty();
}

function lns(v) { return v.split(/\r?\n/).map(s => s.trim()).filter(Boolean); }
function slug(v, i) { return `${v.toLowerCase().replace(/[^a-z0-9]+/gi, "_").replace(/^_|_$/g, "") || "item"}_${i + 1}`; }

function updateList(i, key, value) {
  const step = steps()[i];
  const vals = lns(value);
  if (key === "options") { step.options = vals; step.selection_options = vals.map((l, j) => ({ id: slug(l, j), label: l, label_ru: l })); }
  if (key === "drag_items") { step.drag_items = vals.map((l, j) => ({ id: slug(l, j), label: l, label_ru: l })); step.items = deepCopy(step.drag_items); }
  if (key === "drag_targets") { step.drag_targets = vals.map((l, j) => ({ id: slug(l, j), label: l, label_ru: l })); step.targets = deepCopy(step.drag_targets); }
  if (key === "model_examples") { step.model_examples = vals; step.target_language_options = vals; }
  markDirty();
}

function updateVideo(i, key, value) {
  const step = steps()[i];
  step[key] = Boolean(value);
  if (key === "auto_continue") step.autoContinue = Boolean(value);
  const desc = Array.isArray(step.media_sequence) ? step.media_sequence.find(x => x?.type === "video") : null;
  if (desc) {
    desc[key] = Boolean(value);
    if (key === "auto_continue") desc.autoContinue = Boolean(value);
  }
  markDirty();
}

function moveStep(from, to) {
  const list = steps();
  if (from < 0 || to < 0 || from >= list.length || to >= list.length || from === to) return;
  const [item] = list.splice(from, 1);
  list.splice(to, 0, item);
  renumber();
  markDirty();
  renderSteps();
}

function duplicateStep(i) {
  const copy = deepCopy(steps()[i]);
  copy.slide_id = uniqueId(`${copy.type || "step"}_copy`);
  copy.id = copy.slide_id;
  steps().splice(i + 1, 0, copy);
  renumber();
  markDirty();
  renderSteps();
  notice("Копия шага добавлена.");
}

function deleteStep(i) {
  const step = steps()[i];
  if (!confirm(`Удалить шаг «${step.slide_id || i + 1}»?`)) return;
  steps().splice(i, 1);
  renumber();
  markDirty();
  renderSteps();
}

async function uploadFile(file) {
  if (!file) throw new Error("Сначала выберите файл.");
  const body = new FormData();
  body.append("file", file);
  return api(`/api/studio/lessons/${state.lessonId}/media`, { method: "POST", body });
}

async function replaceStepMedia(i, file) {
  if (!file) return;
  const vid = file.type.startsWith("video/");
  try {
    const asset = await uploadFile(file);
    setMedia(steps()[i], asset.path, vid);
    releaseBlobs();
    markDirty();
    renderSteps();
    notice("Файл загружен. Нажмите «Сохранить».");
  } catch (e) {
    showErrors(e.details?.length ? e.details : e.message);
    renderSteps();
  }
}

function fillPos(sel) {
  sel.innerHTML = Array.from({ length: steps().length + 1 }, (_, i) => `<option value="${i}">${i === steps().length ? "В конец" : `Перед шагом ${i + 1}`}</option>`).join("");
  sel.value = String(steps().length);
}

function openAddDialog(mode) {
  state.dialogMode = mode;
  if ($("#stepDialogTitle")) $("#stepDialogTitle").textContent = mode === "video" ? "Добавить видео" : mode === "exercise" ? "Добавить задание" : "Добавить слайд";
  if ($("#confirmAddStep")) $("#confirmAddStep").textContent = mode === "video" ? "Добавить и сохранить" : "Добавить";
  if ($("#slideFields")) $("#slideFields").classList.toggle("hidden", mode === "video");
  if ($("#videoFields")) $("#videoFields").classList.toggle("hidden", mode !== "video");
  const sp = $("#slidePosition"); if (sp) fillPos(sp);
  const vp = $("#videoPosition"); if (vp) fillPos(vp);
  if ($("#newSlideType")) $("#newSlideType").innerHTML = typeOpts(mode === "exercise" ? "guided_speaking" : "presentation");
  ["newSlideFile", "newVideoFile", "newTargetPhrase", "newNativeExplanation", "newAiInstruction"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = "";
  });
  if ($("#stepDialog")) $("#stepDialog").showModal();
}

async function confirmAdd(ev) {
  ev.preventDefault();
  const btn = $("#confirmAddStep");
  btn.disabled = true;
  btn.textContent = "Добавляем...";
  try {
    let step, pos;
    if (state.dialogMode === "video") {
      const file = $("#newVideoFile")?.files?.[0];
      if (!file) throw new Error("Выберите видеофайл.");
      const asset = await uploadFile(file);
      pos = Number($("#videoPosition")?.value || steps().length);
      const ac = $("#videoAutoContinue")?.checked !== false;
      step = {
        slide_id: uniqueId("video"), type: "video", src: asset.path, video_file: asset.path,
        autoplay: true, auto_continue: ac, autoContinue: ac, skippable: true, replay: true,
        aspect_ratio: "16:9", requiredForMovie: false,
        media_sequence: [{ id: "video", type: "video", src: asset.path, autoplay: true, auto_continue: ac, autoContinue: ac, skippable: true, replay: true }]
      };
    } else {
      pos = Number($("#slidePosition")?.value || steps().length);
      const tgt = ($("#newTargetPhrase")?.value || "").trim();
      const nat = ($("#newNativeExplanation")?.value || "").trim();
      const ai = ($("#newAiInstruction")?.value || "").trim();
      const type = $("#newSlideType")?.value || "presentation";
      const isSpeak = type === "guided_speaking" || type === "guided_scene";
      step = {
        slide_id: uniqueId("step"), type, prompt: tgt || nat || ai || "Новый слайд",
        bot_says_target: tgt, question: tgt, task_goal: tgt, native_explanation: nat,
        bot_says_native: nat, bot_explains_native: nat, native_hint: nat,
        ai_instruction: ai, tutor_instruction: ai, interaction_type: "answer_question",
        pedagogical_intent: "answer_question", open_question_first: true, examples_allowed: true,
        adaptive_scaffolding: true, adaptive: true, requiredForMovie: false, max_attempts: 3,
        controls: { answer: { enabled: isSpeak, required: isSpeak }, continue: { enabled: true, when: isSpeak ? "after_answer" : "always" }, hint: { enabled: true }, follow_up: { enabled: false } },
        answer_mode: isSpeak ? "required_voice" : "none", continue_policy: isSpeak ? "after_answer" : "always",
        hint_enabled: true, follow_up_policy: "none", allow_ai_followup: false
      };
      const file = $("#newSlideFile")?.files?.[0];
      if (file) {
        const asset = await uploadFile(file);
        setMedia(step, asset.path, false);
      }
    }
    steps().splice(Math.max(0, Math.min(pos, steps().length)), 0, step);
    renumber();
    releaseBlobs();
    markDirty();
    renderSteps();
    if ($("#stepDialog")) $("#stepDialog").close();
    if (state.dialogMode === "video") {
      const saved = await save();
      if (saved) notice("Видео загружено и вставлено.");
    } else notice("Шаг добавлен. Нажмите «Сохранить».");
  } catch (e) {
    showErrors(e.details?.length ? e.details : e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = state.dialogMode === "video" ? "Добавить и сохранить" : "Добавить";
  }
}

function candidate() {
  const lesson = deepCopy(state.lesson);
  lesson.title = ($("#lessonTitle")?.value || "").trim();
  lesson.target_language = ($("#lessonTargetLanguage")?.value || "ru").trim();
  lesson.explanation_language = ($("#lessonExplanationLanguage")?.value || "ru").trim();
  lesson.native_language = lesson.explanation_language;
  lesson.difficulty = $("#lessonDifficulty")?.value || "PRE_A1";
  lesson.max_completed_runs = Number($("#lessonMaxRuns")?.value) || 2;
  lesson.max_runs = lesson.max_completed_runs;
  lesson.access_mode = $("#lessonAccessMode")?.value || "free";
  const minAge = Number($("#lessonMinAge")?.value);
  if (minAge > 0) lesson.min_age = minAge;
  lesson.course_id = ($("#lessonCourseSelect")?.value || "conversation").trim();
  lesson.description = ($("#lessonDescription")?.value || "").trim();
  const key = Array.isArray(lesson.steps) ? "steps" : "slides";
  lesson[key] = steps().map((s, i) => ({ ...deepCopy(s), order: i + 1 }));
  return lesson;
}

async function validateCand(lesson) {
  const data = await api(`/api/studio/lessons/${state.lessonId}/validate`, { method: "POST", body: JSON.stringify({ lesson }) });
  if (!data.ok) { showErrors(data.errors); return false; }
  return true;
}

async function save() {
  const btn = $("#saveButton");
  if (btn) { btn.disabled = true; btn.textContent = "Проверяем..."; }
  if ($("#errorPanel")) $("#errorPanel").classList.add("hidden");
  try {
    const lesson = candidate();
    if (!await validateCand(lesson)) return false;
    if (btn) btn.textContent = "Сохраняем...";
    const data = await api(`/api/studio/lessons/${state.lessonId}`, { method: "PUT", body: JSON.stringify({ lesson }) });
    state.lesson = deepCopy(data.lesson);
    state.dirty = false;
    state.versions = data.backup_version ? [data.backup_version, ...state.versions] : state.versions;
    if (btn) btn.textContent = "Сохранить урок";
    notice(data.backup_version ? `Сохранено. Резервная версия: ${data.backup_version}` : "Сохранено.");
    await loadLessons();
    return true;
  } catch (e) {
    showErrors(e.details?.length ? e.details : e.message);
    return false;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = state.dirty ? "Сохранить урок •" : "Сохранить урок"; }
  }
}

async function publish() {
  if (state.dirty && !await save()) return;
  if (!confirm("Опубликовать черновик урока? Дети увидят новую версию урока.")) return;
  try {
    const data = await api(`/api/studio/lessons/${state.lessonId}/publish`, { method: "POST" });
    state.lesson = deepCopy(data.lesson);
    state.dirty = false;
    notice("Урок опубликован. Предыдущая версия сохранена для отката.");
    await openLesson(state.lessonId);
    await loadLessons();
  } catch (e) {
    showErrors(e.details?.length ? e.details : e.message);
  }
}

async function preview() {
  const lesson = candidate();
  if (!await validateCand(lesson)) return;
  const host = $("#previewContent");
  if (!host) return;
  host.innerHTML = "";
  const techEl = $("#previewTechnical");
  if (techEl) techEl.textContent = JSON.stringify(lesson, null, 2);
  const previewUrl = `${location.origin}/api/studio/lessons/${state.lessonId}/preview`;
  const qrUrl = `https://api.qrserver.com/v1/create-qr-code/?size=160x160&data=${encodeURIComponent(previewUrl)}`;
  const qrPanel = $("#previewQR");
  if (qrPanel) qrPanel.innerHTML = `<img src="${qrUrl}" alt="QR" style="border-radius:8px;border:2px solid #E2E8F0"><p style="font-size:11px;text-align:center;color:#64748B;margin-top:4px">Открыть в браузере</p><a href="${previewUrl}" target="_blank" style="font-size:11px;color:#246BFD">JSON предпросмотр ↗</a>`;
  const slideList = Array.isArray(lesson.steps) ? lesson.steps : (lesson.slides || []);
  for (const [i, step] of slideList.entries()) {
    const vid = isVideo(step), src = sourceOf(step);
    const card = document.createElement("article");
    card.className = "preview-step";
    card.innerHTML = `<div class="preview-media">${src ? "Загружаем..." : "Нет изображения"}</div><div class="preview-copy"><div class="eyebrow">${i + 1} · ${escH(vid ? "Видео" : (TYPE_LABEL[step.type] || step.type || "Слайд"))}</div><h3>${escH(valFrom(step, "bot_says_target", ["question", "task_goal", "prompt"]) || step.slide_id)}</h3><p>${escH(valFrom(step, "bot_says_native", ["bot_explains_native", "native_hint"]))}</p><p class="muted">${escH(valFrom(step, "ai_instruction", ["tutor_instruction"]))}</p></div>`;
    host.append(card);
    if (src) renderMedia(card.querySelector(".preview-media"), src, vid);
  }
  if ($("#previewDialog")) $("#previewDialog").showModal();
}

function showVersions() {
  const host = $("#versionList");
  if (!host) return;
  host.innerHTML = state.versions.length ? "" : '<p class="muted">Резервных версий пока нет.</p>';
  state.versions.forEach(v => {
    const row = document.createElement("div");
    row.className = "version-row";
    row.innerHTML = `<span>${escH(v)}</span><button type="button">Восстановить</button>`;
    row.querySelector("button").addEventListener("click", () => restoreVersion(v));
    host.append(row);
  });
  if ($("#versionDialog")) $("#versionDialog").showModal();
}

async function restoreVersion(v) {
  if (!confirm("Создать черновик из резервной версии? Опубликованный урок не меняется.")) return;
  try {
    await api(`/api/studio/lessons/${state.lessonId}/rollback`, { method: "POST", body: JSON.stringify({ version: v, as_draft: true }) });
    if ($("#versionDialog")) $("#versionDialog").close();
    await openLesson(state.lessonId);
    notice("Резервная версия восстановлена в черновик.");
  } catch (e) {
    showErrors(e.details?.length ? e.details : e.message);
  }
}

function openLessonDialog(mode, courseId = null, existingLesson = null) {
  state.lessonDialogMode = mode;
  const dup = mode === "duplicate";
  if ($("#lessonDialogTitle")) $("#lessonDialogTitle").textContent = dup ? "Дублировать урок" : "Новый урок";
  if ($("#confirmLessonAction")) $("#confirmLessonAction").textContent = dup ? "Создать копию" : "Создать урок";
  if ($("#newLessonId")) $("#newLessonId").value = dup ? `${existingLesson?.lesson_id || state.lessonId}_copy` : "";
  if ($("#newLessonTitle")) $("#newLessonTitle").value = dup ? `${existingLesson?.title || state.lesson?.title || state.lessonId} — копия` : "";
  if ($("#newLessonTargetLanguage")) $("#newLessonTargetLanguage").value = existingLesson?.target_language || state.lesson?.target_language || "ru";
  if ($("#newLessonExplanationLanguage")) $("#newLessonExplanationLanguage").value = existingLesson?.explanation_language || state.lesson?.explanation_language || "ru";

  const sel = $("#newLessonCourseSelect");
  if (sel) {
    sel.innerHTML = state.courses.map(c => `<option value="${escH(c.id)}">${escH(c.title || c.id)}</option>`).join("");
    if (courseId) sel.value = courseId;
    else if (existingLesson?.course_id) sel.value = existingLesson.course_id;
  }
  if ($("#lessonDialog")) $("#lessonDialog").showModal();
}

function slugifyLessonTitle(title) {
  const ruMap = {'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'e','ж':'zh','з':'z','и':'i','й':'y','к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'h','ц':'ts','ч':'ch','ш':'sh','щ':'sch','ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya'};
  let s = String(title || '').toLowerCase().trim();
  let res = '';
  for (const ch of s) {
    if (ruMap[ch] !== undefined) res += ruMap[ch];
    else if (/[a-z0-9]/.test(ch)) res += ch;
    else if (ch === ' ' || ch === '-' || ch === '_') res += '_';
  }
  res = res.replace(/_+/g, '_').replace(/^_+|_+$/g, '');
  const ts = Date.now().toString(36);
  return res ? `${res.slice(0, 30)}_${ts.slice(-4)}` : `lesson_${ts}`;
}

async function confirmLessonAction(ev) {
  ev.preventDefault();
  let id = ($("#newLessonId")?.value || "").trim().toLowerCase();
  const title = ($("#newLessonTitle")?.value || "").trim();
  if (!title) { showErrors("Введите название урока."); return; }
  if (!id) {
    id = slugifyLessonTitle(title);
  } else if (!/^[a-z0-9][a-z0-9_-]{1,79}$/.test(id)) {
    id = slugifyLessonTitle(id);
  }
  const btn = $("#confirmLessonAction");
  btn.disabled = true;
  try {
    const body = {
      lesson_id: id,
      title,
      course_id: $("#newLessonCourseSelect")?.value || "conversation",
      target_language: ($("#newLessonTargetLanguage")?.value || "ru").trim(),
      explanation_language: ($("#newLessonExplanationLanguage")?.value || "ru").trim()
    };
    const path = state.lessonDialogMode === "duplicate" ? `/api/studio/lessons/${state.lessonId}/duplicate` : "/api/studio/lessons";
    await api(path, { method: "POST", body: JSON.stringify(body) });
    if ($("#lessonDialog")) $("#lessonDialog").close();
    state.dirty = false;
    await loadCourses();
    await loadLessons();
    await openLesson(id);
    notice(state.lessonDialogMode === "duplicate" ? "Копия урока создана." : "Урок создан.");
  } catch (e) {
    showErrors(e.details?.length ? e.details : e.message);
  } finally { btn.disabled = false; }
}

async function toggleLesson() {
  if (!state.lessonId) return;
  if (state.dirty && !await save()) return;
  if (state.summary?.published) {
    if (!confirm("Отключить урок?")) return;
    try {
      await api(`/api/studio/lessons/${state.lessonId}/archive`, { method: "POST" });
      await openLesson(state.lessonId);
      await loadLessons();
      notice("Урок отключён.");
    } catch (e) { showErrors(e.details?.length ? e.details : e.message); }
  } else await publish();
}

async function deleteLesson(lessonId = null) {
  const id = lessonId || state.lessonId;
  if (!id) return;
  const lObj = state.lessons.find(l => l.lesson_id === id);
  if (!confirm(`Удалить урок «${lObj?.title || id}»?`)) return;
  if (!confirm("Подтвердите ещё раз.")) return;
  try {
    await api(`/api/studio/lessons/${id}`, { method: "DELETE" });
    if (state.lessonId === id) {
      releaseBlobs();
      state.lesson = null;
      state.summary = null;
      state.lessonId = "";
      state.dirty = false;
      if ($("#editorBody")) $("#editorBody").classList.add("hidden");
      if ($("#emptyState")) $("#emptyState").classList.remove("hidden");
    }
    await loadLessons();
    notice("Урок удален.");
  } catch (e) { showErrors(e.details?.length ? e.details : e.message); }
}

/* ==========================================================================
   HOMEWORK BUILDER & CMS
   ========================================================================== */

async function loadHomework(lessonId, render = true) {
  try {
    const data = await api(`/api/studio/lessons/${lessonId}/homework`);
    state.homework = deepCopy(data.homework);
    state.hwDirty = false;

    // Update tab badge
    const badge = $("#tabHwStatusBadge");
    if (badge) {
      const en = state.homework.enabled;
      badge.textContent = en ? "ON" : "OFF";
      badge.className = "badge-status " + (en ? "published" : "draft");
    }

    if (render) renderHomework();
  } catch (e) {
    console.warn("Failed to load homework:", e);
  }
}

function renderHomework() {
  if (!state.homework) return;
  const hw = state.homework;

  if ($("#hwIdLabel")) $("#hwIdLabel").textContent = `ДЗ · ${state.lessonId}`;
  if ($("#hwTitle")) $("#hwTitle").value = hw.title || `ДЗ: ${state.lesson?.title || state.lessonId}`;
  if ($("#hwMeta")) $("#hwMeta").textContent = `Привязано к уроку: ${state.lesson?.title || state.lessonId} · Статус: ${hw.status || 'published'}`;
  if ($("#hwEnabled")) $("#hwEnabled").checked = hw.enabled !== false;
  if ($("#hwAvailablePolicy")) $("#hwAvailablePolicy").value = hw.available_policy || "immediate";
  if ($("#hwOptional")) $("#hwOptional").value = hw.optional !== false ? "optional" : "mandatory";
  if ($("#hwBlocksNextLesson")) $("#hwBlocksNextLesson").checked = hw.requires_completion_for_next_lesson === true;
  if ($("#hwDuration")) $("#hwDuration").value = hw.estimated_duration_minutes || 5;
  if ($("#hwDescription")) $("#hwDescription").value = hw.description || "";

  renderHomeworkSteps();
}

function renderHomeworkSteps() {
  const host = $("#hwSteps");
  if (!host) return;
  host.innerHTML = "";
  const list = hwSteps();

  list.forEach((step, i) => {
    step.order = i + 1;
    if (!step.id) step.id = `hw_step_${i + 1}`;
    host.appendChild(renderHwStepCard(step, i));
  });

  if (!list.length) {
    host.innerHTML = '<div class="empty card"><h3>В домашнем задании пока нет шагов</h3><p>Нажмите «+ Рисование», «+ Голосовой ответ» или «+ Выбор ответа» выше.</p></div>';
  }
}

function renderHwStepCard(step, index) {
  const card = document.createElement("article");
  card.className = "step-card";
  card.draggable = true;

  const type = step.type || "drawing";
  const typeLabel = HW_TYPE_LABEL[type] || type;

  card.innerHTML = `
    <div class="step-summary">
      <span class="drag-handle" title="Перетащить">&#x22EE;&#x22EE;</span>
      <span class="step-number">${index + 1}</span>
      <span class="step-badge ${type === 'video' ? 'video' : ''}">${escH(typeLabel)}</span>
      <strong class="step-name grow">${escH(step.prompt || step.bot_says_target || `Задание ${index + 1}`)}</strong>
      <button class="move-up icon" title="Выше">&#x2191;</button>
      <button class="move-down icon" title="Ниже">&#x2193;</button>
      <button class="duplicate-hw-step" title="Дублировать">Копия</button>
      <button class="delete-hw-step danger" title="Удалить">Удалить</button>
      <button class="toggle-expand icon" title="Развернуть/свернуть">&#x25BC;</button>
    </div>
    <div class="step-body">
      <div class="step-grid">
        <div class="media-column">
          <div class="media-preview">${step.image || step.src ? "Медиа выбрано" : "Без медиа"}</div>
          <label class="file-button">Загрузить медиа<input class="replace-hw-media" type="file"></label>
          <div class="media-path muted">${escH(step.image || step.src || "")}</div>
        </div>
        <div class="fields-column">
          <div class="two">
            <label>ID шага<input data-hw-field="id" value="${escH(step.id || '')}"></label>
            <label>Тип шага<select data-hw-field="type">
              ${HW_TYPES.map(([k, v]) => `<option value="${k}"${k === type ? ' selected' : ''}>${escH(v)}</option>`).join("")}
            </select></label>
          </div>
          <label>Фраза / Задание на изучаемом языке (prompt / bot_says_target)<input data-hw-field="prompt" value="${escH(step.prompt || step.bot_says_target || '')}" placeholder="Draw the sun and say Sun"></label>
          <label>Объяснение ребёнку на родном языке (bot_says_native)<textarea data-hw-field="bot_says_native" rows="2" placeholder="Нарисуй солнышко и произнеси Sun">${escH(step.bot_says_native || step.native_explanation || '')}</textarea></label>
          <label>Инструкция для AI проверки (ai_instruction)<textarea data-hw-field="ai_instruction" rows="2" placeholder="Похвали ребёнка если он нарисовал круглое желтое солнце">${escH(step.ai_instruction || '')}</textarea></label>

          <div class="hw-choice-options ${type === 'choice' ? '' : 'hidden'}">
            <label>Варианты выбора (по одному в строке)<textarea data-hw-field="options_raw" rows="3">${escH(listText(step.options || []))}</textarea></label>
            <label>Правильный ответ<input data-hw-field="correct_answer" value="${escH(step.correct_answer || '')}"></label>
          </div>

          <div class="hw-voice-options ${type === 'voice_answer' || type === 'repeat' ? '' : 'hidden'}">
            <label>Примеры правильного произношения (по одному в строке)<textarea data-hw-field="model_examples_raw" rows="3">${escH(listText(step.model_examples || []))}</textarea></label>
          </div>
        </div>
      </div>
    </div>
  `;

  // Bind inputs
  card.querySelectorAll("[data-hw-field]").forEach(input => {
    input.addEventListener("input", (ev) => {
      const field = ev.target.dataset.hwField;
      const val = ev.target.value;
      if (field === "type") {
        step.type = val;
        renderHomeworkSteps();
      } else if (field === "options_raw") {
        step.options = lns(val);
      } else if (field === "model_examples_raw") {
        step.model_examples = lns(val);
      } else {
        step[field] = val;
      }
      markHwDirty();
    });
  });

  // Media upload for hw step
  card.querySelector(".replace-hw-media")?.addEventListener("change", async (ev) => {
    const file = ev.target.files?.[0];
    if (!file) return;
    try {
      const asset = await uploadFile(file);
      step.image = asset.path;
      step.src = asset.path;
      markHwDirty();
      renderHomeworkSteps();
      notice("Медиа файл прикреплен к ДЗ.");
    } catch (e) {
      showErrors(e.details?.length ? e.details : e.message);
    }
  });

  // Reorder & actions
  card.querySelector(".move-up")?.addEventListener("click", () => moveHwStep(index, index - 1));
  card.querySelector(".move-down")?.addEventListener("click", () => moveHwStep(index, index + 1));
  card.querySelector(".duplicate-hw-step")?.addEventListener("click", () => duplicateHwStep(index));
  card.querySelector(".delete-hw-step")?.addEventListener("click", () => deleteHwStep(index));
  card.querySelector(".toggle-expand")?.addEventListener("click", () => {
    const b = card.querySelector(".step-body");
    const col = b.style.display === "none";
    b.style.display = col ? "" : "none";
    card.querySelector(".toggle-expand").textContent = col ? "▼" : "▶";
  });

  // Drag and drop
  card.addEventListener("dragstart", (ev) => {
    state.hwDragIndex = index;
    card.classList.add("dragging");
    ev.dataTransfer.effectAllowed = "move";
  });
  card.addEventListener("dragend", () => {
    state.hwDragIndex = null;
    $$("#hwSteps .step-card").forEach(c => c.classList.remove("dragging", "drop-before"));
  });
  card.addEventListener("dragover", (ev) => {
    ev.preventDefault();
    card.classList.add("drop-before");
  });
  card.addEventListener("dragleave", () => card.classList.remove("drop-before"));
  card.addEventListener("drop", (ev) => {
    ev.preventDefault();
    card.classList.remove("drop-before");
    if (state.hwDragIndex !== null && state.hwDragIndex !== index) {
      moveHwStep(state.hwDragIndex, index);
    }
  });

  return card;
}

function moveHwStep(from, to) {
  const list = hwSteps();
  if (from < 0 || to < 0 || from >= list.length || to >= list.length || from === to) return;
  const [item] = list.splice(from, 1);
  list.splice(to, 0, item);
  markHwDirty();
  renderHomeworkSteps();
}

function duplicateHwStep(index) {
  const list = hwSteps();
  const copy = deepCopy(list[index]);
  copy.id = `hw_step_${list.length + 1}`;
  list.splice(index + 1, 0, copy);
  markHwDirty();
  renderHomeworkSteps();
  notice("Шаг ДЗ скопирован.");
}

function deleteHwStep(index) {
  const list = hwSteps();
  if (!confirm(`Удалить шаг ДЗ ${index + 1}?`)) return;
  list.splice(index, 1);
  markHwDirty();
  renderHomeworkSteps();
}

function addHwStep(type) {
  const list = hwSteps();
  const id = `hw_step_${list.length + 1}`;
  let newStep = { id, type, order: list.length + 1 };

  if (type === "drawing") {
    newStep.prompt = "Draw and color the picture";
    newStep.bot_says_native = "Нарисуй рисунок на свободную тему или по уроку!";
    newStep.ai_instruction = "Похвали ребёнка за цвета и творчество.";
  } else if (type === "voice_answer") {
    newStep.prompt = "Answer the question by voice";
    newStep.bot_says_native = "Произнеси ответ вслух!";
    newStep.ai_instruction = "Проверь правильность ключевых слов.";
    newStep.model_examples = ["yes", "no"];
  } else if (type === "choice") {
    newStep.prompt = "Choose the right answer";
    newStep.bot_says_native = "Выбери правильный вариант!";
    newStep.options = ["Option 1", "Option 2", "Option 3"];
    newStep.correct_answer = "Option 1";
  } else if (type === "video") {
    newStep.prompt = "Watch the homework clip";
    newStep.bot_says_native = "Посмотри короткое обучающее видео!";
  } else {
    newStep.prompt = "Review the information";
    newStep.bot_says_native = "Внимательно изучи эту карточку!";
  }

  list.push(newStep);
  markHwDirty();
  renderHomeworkSteps();
  notice(`Добавлен шаг «${HW_TYPE_LABEL[type] || type}».`);
}

function hwCandidate() {
  const hw = deepCopy(state.homework || {});
  hw.lesson_id = state.lessonId;
  hw.title = ($("#hwTitle")?.value || "").trim() || `ДЗ: ${state.lesson?.title || state.lessonId}`;
  hw.enabled = $("#hwEnabled")?.checked !== false;
  hw.available_policy = $("#hwAvailablePolicy")?.value || "immediate";
  hw.optional = $("#hwOptional")?.value === "optional";
  hw.requires_completion_for_next_lesson = $("#hwBlocksNextLesson")?.checked === true;
  hw.estimated_duration_minutes = Number($("#hwDuration")?.value) || 5;
  hw.description = ($("#hwDescription")?.value || "").trim();
  hw.slides = hwSteps().map((s, i) => ({ ...deepCopy(s), order: i + 1 }));
  return hw;
}

async function saveHomework() {
  const btn = $("#saveHwBtn");
  if (btn) { btn.disabled = true; btn.textContent = "Сохраняем..."; }
  try {
    const payload = hwCandidate();
    const data = await api(`/api/studio/lessons/${state.lessonId}/homework`, {
      method: "PUT",
      body: JSON.stringify({ homework: payload })
    });
    state.homework = deepCopy(data.homework);
    state.hwDirty = false;
    if (btn) btn.textContent = "Сохранить ДЗ";
    notice("Домашнее задание сохранено.");
    await loadHomework(state.lessonId);
    await loadLessons();
  } catch (e) {
    showErrors(e.details?.length ? e.details : e.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function publishHomework() {
  if (state.hwDirty && !await saveHomework()) return;
  if (!confirm("Опубликовать домашнее задание для учеников?")) return;
  try {
    const data = await api(`/api/studio/lessons/${state.lessonId}/homework/publish`, { method: "POST" });
    state.homework = deepCopy(data.homework);
    notice("Домашнее задание опубликовано.");
    await loadHomework(state.lessonId);
    await loadLessons();
  } catch (e) {
    showErrors(e.details?.length ? e.details : e.message);
  }
}

async function duplicateHomework() {
  if (!confirm("Создать копию домашнего задания?")) return;
  try {
    const data = await api(`/api/studio/lessons/${state.lessonId}/homework/duplicate`, { method: "POST" });
    notice("Копия ДЗ создана.");
    await loadHomework(state.lessonId);
  } catch (e) {
    showErrors(e.details?.length ? e.details : e.message);
  }
}

async function deleteHomework() {
  if (!confirm("Архивировать домашнее задание этого урока?")) return;
  try {
    await api(`/api/studio/lessons/${state.lessonId}/homework`, { method: "DELETE" });
    notice("Домашнее задание переведено в архив.");
    await loadHomework(state.lessonId);
    await loadLessons();
  } catch (e) {
    showErrors(e.details?.length ? e.details : e.message);
  }
}

function openMoveHomeworkDialog() {
  if ($("#moveHwNotice")) {
    $("#moveHwNotice").textContent = `Текущий урок: ${state.lesson?.title || state.lessonId}`;
  }
  const sel = $("#targetLessonSelect");
  if (sel) {
    sel.innerHTML = state.lessons.filter(l => l.lesson_id !== state.lessonId).map(l => `<option value="${escH(l.lesson_id)}">${escH(l.title || l.lesson_id)} (${escH(l.course_id || 'conversation')})</option>`).join("");
  }
  if ($("#moveHomeworkDialog")) $("#moveHomeworkDialog").showModal();
}

async function confirmMoveHomework(ev) {
  ev.preventDefault();
  const targetLessonId = $("#targetLessonSelect")?.value;
  if (!targetLessonId) return;
  try {
    await api(`/api/studio/lessons/${state.lessonId}/homework/move`, {
      method: "POST",
      body: JSON.stringify({ target_lesson_id: targetLessonId })
    });
    if ($("#moveHomeworkDialog")) $("#moveHomeworkDialog").close();
    notice("Домашнее задание перенесено.");
    await loadHomework(state.lessonId);
    await loadLessons();
  } catch (e) {
    showErrors(e.details?.length ? e.details : e.message);
  }
}

/* ==========================================================================
   USERS MANAGEMENT & OWNER ACCESS
   ========================================================================== */

async function loadUsers(email = "") {
  let url = "/api/studio/admin/users?limit=100";
  if (email) url += `&email=${encodeURIComponent(email)}`;
  try {
    const data = await api(url);
    renderUsers(data.users || []);
  } catch (e) {
    const host = $("#userSearchResults");
    if (host) host.innerHTML = `<p class="muted">Ошибка: ${escH(e.message)}</p>`;
  }
}

function renderUsers(users) {
  const host = $("#userSearchResults");
  if (!host) return;
  host.innerHTML = "";
  if (!users.length) {
    host.innerHTML = '<p class="muted" style="padding:12px">Пользователи не найдены.</p>';
    return;
  }
  users.forEach(u => {
    const row = document.createElement("div");
    row.className = "user-row";
    const role = u.account_role || "STANDARD";
    const cc = (u.children || []).length;
    row.innerHTML = `
      <span class="user-email">${escH(u.email)}</span>
      <span class="user-role-badge role-${escH(role)}">${escH(role)}</span>
      <span class="muted" style="font-size:12px">${cc} ${cc === 1 ? "ребёнок" : "детей"}</span>
      <button type="button" class="srb">Роль</button>
      <button type="button" class="sdb">Детали</button>
    `;
    row.querySelector(".srb").addEventListener("click", ev => { ev.stopPropagation(); openRoleDialog(u); });
    row.querySelector(".sdb").addEventListener("click", ev => { ev.stopPropagation(); loadUserDetail(u.id); });
    host.append(row);
  });
}

async function loadUserDetail(pid) {
  try {
    const u = await api(`/api/studio/admin/users/${pid}`);
    const host = $("#userDetail");
    if (!host) return;
    host.classList.remove("hidden");
    let entsHtml = "";
    if (u.entitlements && u.entitlements.length) {
      entsHtml = `
        <h4 style="margin:12px 0 6px">Прогресс по урокам</h4>
        <table class="entitlement-table">
          <tr><th>Ребёнок</th><th>Урок</th><th>Пройдено</th><th>Макс</th><th>Статус</th></tr>
          ${u.entitlements.map(e => `<tr><td>${e.child_id}</td><td>${escH(e.lesson_id)}</td><td>${e.completed_runs}</td><td>${e.max_completed_runs}</td><td>${escH(e.status)}</td></tr>`).join("")}
        </table>
      `;
    }
    host.innerHTML = `
      <h3>${escH(u.email)}</h3>
      <div style="margin:8px 0"><strong>Роль:</strong> <span class="user-role-badge role-${escH(u.account_role || "STANDARD")}">${escH(u.account_role || "STANDARD")}</span> <button type="button" id="udRoleBtn">Изменить роль</button></div>
      <div style="margin:8px 0"><strong>Дети:</strong> ${(u.children || []).map(c => `${escH(c.display_name || "?")} (${c.age_years || "?"}л, ${c.target_language || "?"})`).join(", ") || "нет"}</div>
      ${entsHtml}
    `;
    const crb = host.querySelector("#udRoleBtn");
    if (crb) crb.addEventListener("click", () => openRoleDialog(u));
  } catch (e) { console.warn("Detail err", e); }
}

function openRoleDialog(u) {
  state.pendingRoleParentId = u.id;
  if ($("#roleDialogEmail")) $("#roleDialogEmail").textContent = `${u.email} (ID: ${u.id})`;
  if ($("#roleSelect")) $("#roleSelect").value = u.account_role || "STANDARD";
  if ($("#roleDialog")) $("#roleDialog").showModal();
}

async function applyRole(ev) {
  ev.preventDefault();
  if (!state.pendingRoleParentId) return;
  const role = $("#roleSelect")?.value;
  try {
    const data = await api(`/api/studio/admin/users/${state.pendingRoleParentId}/role`, { method: "POST", body: JSON.stringify({ role }) });
    if ($("#roleDialog")) $("#roleDialog").close();
    notice(`Роль изменена: ${data.old_role} → ${data.new_role}`);
    await loadUsers(($("#userSearch")?.value || "").trim());
  } catch (e) { alert(e.message); }
}

async function applyOwnerAllowlist() {
  if (!confirm("Применить Owner Allowlist? Все email из списка получат роль OWNER.")) return;
  try {
    const data = await api("/api/studio/admin/owner-apply", { method: "POST" });
    const applied = (data.applied || []).map(a => `${a.email} (${a.old_role}→OWNER)`).join(", ") || "—";
    const skipped = (data.skipped || []).map(s => `${s.email}: ${s.reason}`).join(", ") || "—";
    alert(`Применено: ${applied}\nПропущено: ${skipped}`);
    await loadUsers();
  } catch (e) { alert(e.message); }
}

async function loadDashboard() {
  try {
    const dashboard = await api("/api/studio/dashboard");
    const host = $("#dashboardContent");
    if (!host) return;
    host.innerHTML = "";
    [
      { label: "Пользователи", value: dashboard.total_users },
      { label: "Активные дети", value: dashboard.active_children },
      { label: "Активные подписки", value: dashboard.active_subscriptions },
      { label: "Курсы", value: dashboard.courses },
      { label: "Опубликовано уроков", value: dashboard.published_lessons },
      { label: "Черновики", value: dashboard.draft_lessons },
      { label: "Завершено уроков", value: dashboard.lessons_completed },
      { label: "Ошибки медиа", value: dashboard.media_issues },
      { label: "Ошибки валидации", value: dashboard.validation_issues },
      { label: "Недавние runtime-ошибки", value: dashboard.recent_errors ?? "N/A" }
    ].forEach(s => {
      const card = document.createElement("div");
      card.className = "stat-card";
      card.innerHTML = `<div class="stat-value">${escH(String(s.value))}</div><div class="stat-label">${escH(s.label)}</div>`;
      host.append(card);
    });
  } catch (e) { console.warn("Dashboard err", e); }
}

/* ==========================================================================
   BOOT & EVENT BINDINGS
   ========================================================================== */

function bind(id, fn, ev = "click") {
  const el = document.getElementById(id);
  if (el) el.addEventListener(ev, fn);
}


/* ==========================================================================
   PROMO CODES MANAGEMENT (Stage 7 & 8)
   ========================================================================== */

const BENEFIT_LABELS = {
  PERCENTAGE: "Скидка %",
  FIXED_AMOUNT: "Фиксированная скидка",
  SPECIAL_PRICE: "Спеццена",
  FREE_PERIOD_DAYS: "Бесплатный период",
  EXTRA_LESSONS: "+Уроки в подарок",
  EXTRA_COURSE: "+Курс в подарок",
  TRIAL_OFFER: "Пробный период",
  N_PERIODS_DISCOUNT: "Скидка на N периодов",
  PERMANENT_SPECIAL_PRICE: "Постоянная спеццена"
};

async function loadPromos() {
  const tbody = $("#promoTableBody");
  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="8" style="padding: 24px; text-align: center; color: #94a3b8;">Загрузка...</td></tr>';
  try {
    const data = await api("/api/studio/promos");
    state.promos = data.promos || [];
    renderPromos();
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="8" style="padding: 24px; text-align: center; color: #ef4444;">Ошибка: ${escH(err.message)}</td></tr>`;
  }
}

function renderPromos() {
  const tbody = $("#promoTableBody");
  if (!tbody) return;
  if (!state.promos || state.promos.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" style="padding: 24px; text-align: center; color: #94a3b8;">Промокодов пока нет. Создайте первый промокод!</td></tr>';
    return;
  }

  tbody.innerHTML = state.promos.map(p => {
    const typeLabel = BENEFIT_LABELS[p.benefit_type] || p.benefit_type;
    let valStr = `${p.benefit_value}`;
    if (p.benefit_type === "PERCENTAGE" || p.benefit_type === "N_PERIODS_DISCOUNT") valStr += "%";
    else if (p.benefit_type === "FIXED_AMOUNT" || p.benefit_type === "SPECIAL_PRICE" || p.benefit_type === "PERMANENT_SPECIAL_PRICE" || p.benefit_type === "TRIAL_OFFER") valStr += ` ${p.currency}`;
    else if (p.benefit_type === "FREE_PERIOD_DAYS") valStr += " дн.";
    else if (p.benefit_type === "EXTRA_LESSONS") valStr = `+${p.benefit_value} ур.`;

    const validFrom = p.valid_from ? new Date(p.valid_from).toLocaleDateString("ru-RU") : "";
    const validUntil = p.valid_until ? new Date(p.valid_until).toLocaleDateString("ru-RU") : "";
    const dateStr = (validFrom || validUntil) ? `${validFrom || "—"} … ${validUntil || "—"}` : "Бессрочно";

    const maxUsesStr = p.max_uses ? `${p.usage_count} / ${p.max_uses}` : `${p.usage_count} (безлим.)`;
    const statusBadge = p.active
      ? '<span style="background: rgba(34,197,94,.15); color: #22c55e; padding: 4px 8px; border-radius: 6px; font-weight: 600; font-size: 12px;">Активен</span>'
      : '<span style="background: rgba(239,68,68,.15); color: #ef4444; padding: 4px 8px; border-radius: 6px; font-weight: 600; font-size: 12px;">Отключен</span>';

    return `
      <tr style="border-bottom: 1px solid #1e293b;">
        <td style="padding: 12px 8px; font-weight: 700; font-family: monospace; font-size: 15px; color: #38bdf8;">${escH(p.code)}</td>
        <td style="padding: 12px 8px;">${escH(typeLabel)}</td>
        <td style="padding: 12px 8px; font-weight: 600;">${escH(valStr)}</td>
        <td style="padding: 12px 8px; color: #94a3b8; font-size: 13px;">${escH(p.description || "—")}</td>
        <td style="padding: 12px 8px; font-size: 13px; color: #cbd5e1;">${escH(dateStr)}</td>
        <td style="padding: 12px 8px; font-size: 13px;">${escH(maxUsesStr)}</td>
        <td style="padding: 12px 8px;">${statusBadge}</td>
        <td style="padding: 12px 8px; text-align: right; white-space: nowrap;">
          <button class="small-btn" onclick="editPromo(${p.id})" style="margin-right: 6px;">Редактировать</button>
          <button class="small-btn ${p.active ? 'secondary' : 'success'}" onclick="togglePromo(${p.id})" style="margin-right: 6px;">${p.active ? 'Отключить' : 'Включить'}</button>
          <button class="small-btn danger" onclick="deletePromo(${p.id})">Удалить</button>
        </td>
      </tr>
    `;
  }).join("");
}

function openPromoDialog(promo = null) {
  const dlg = $("#promoDialog");
  if (!dlg) return;
  $("#promoDialogTitle").textContent = promo ? `Редактирование: ${promo.code}` : "Новый промокод";
  $("#promoIdInput").value = promo ? promo.id : "";
  $("#promoCodeInput").value = promo ? promo.code : "";
  $("#promoCodeInput").disabled = Boolean(promo);
  $("#promoBenefitTypeSelect").value = promo ? promo.benefit_type : "PERCENTAGE";
  $("#promoBenefitValueInput").value = promo ? promo.benefit_value : "20";
  $("#promoCurrencySelect").value = promo ? (promo.currency || "EUR") : "EUR";
  $("#promoDurationPeriodsInput").value = promo ? (promo.duration_periods || "") : "";
  $("#promoDescriptionInput").value = promo ? (promo.description || "") : "";
  $("#promoValidFromInput").value = promo && promo.valid_from ? promo.valid_from.substring(0, 16) : "";
  $("#promoValidUntilInput").value = promo && promo.valid_until ? promo.valid_until.substring(0, 16) : "";
  $("#promoMaxUsesInput").value = promo && promo.max_uses ? promo.max_uses : "";
  $("#promoMaxUsesPerUserInput").value = promo ? (promo.max_uses_per_user || 1) : 1;
  $("#promoAllowedPlansInput").value = promo && promo.allowed_plan_ids ? promo.allowed_plan_ids.join(", ") : "";
  $("#promoAllowedCoursesInput").value = promo && promo.allowed_course_ids ? promo.allowed_course_ids.join(", ") : "";
  $("#promoNewUsersOnlyCheck").checked = Boolean(promo && promo.new_users_only);
  $("#promoActiveCheck").checked = promo ? Boolean(promo.active) : true;

  const durationRow = $("#promoDurationRow");
  if (durationRow) {
    durationRow.style.display = $("#promoBenefitTypeSelect").value === "N_PERIODS_DISCOUNT" ? "block" : "none";
  }

  dlg.showModal();
}

window.editPromo = function(id) {
  const promo = (state.promos || []).find(p => p.id === id);
  if (promo) openPromoDialog(promo);
};

window.togglePromo = async function(id) {
  try {
    await api(`/api/studio/promos/${id}/toggle`, { method: "POST" });
    notice("Статус промокода изменён");
    await loadPromos();
  } catch (err) {
    showErrors(err.message || "Не удалось переключить статус");
  }
};

window.deletePromo = async function(id) {
  if (!confirm("Вы уверены, что хотите удалить этот промокод?")) return;
  try {
    await api(`/api/studio/promos/${id}`, { method: "DELETE" });
    notice("Промокод удалён");
    await loadPromos();
  } catch (err) {
    showErrors(err.message || "Не удалось удалить промокод");
  }
};

async function savePromo(ev) {
  ev.preventDefault();
  const id = ($("#promoIdInput")?.value || "").trim();
  const code = ($("#promoCodeInput")?.value || "").trim().toUpperCase();
  const benefitType = $("#promoBenefitTypeSelect")?.value || "PERCENTAGE";
  const benefitValue = parseFloat($("#promoBenefitValueInput")?.value || "0");
  const currency = $("#promoCurrencySelect")?.value || "EUR";
  const durationPeriods = $("#promoDurationPeriodsInput")?.value ? parseInt($("#promoDurationPeriodsInput").value) : null;
  const description = ($("#promoDescriptionInput")?.value || "").trim();
  const validFrom = $("#promoValidFromInput")?.value || null;
  const validUntil = $("#promoValidUntilInput")?.value || null;
  const maxUses = $("#promoMaxUsesInput")?.value ? parseInt($("#promoMaxUsesInput").value) : null;
  const maxUsesPerUser = parseInt($("#promoMaxUsesPerUserInput")?.value || "1");
  const allowedPlansRaw = ($("#promoAllowedPlansInput")?.value || "").trim();
  const allowedCoursesRaw = ($("#promoAllowedCoursesInput")?.value || "").trim();
  const newUsersOnly = Boolean($("#promoNewUsersOnlyCheck")?.checked);
  const active = Boolean($("#promoActiveCheck")?.checked);

  if (!code && !id) {
    showErrors("Введите код промокода");
    return;
  }

  const payload = {
    code,
    benefit_type: benefitType,
    benefit_value: benefitValue,
    currency,
    duration_periods: durationPeriods,
    description,
    valid_from: validFrom ? new Date(validFrom).toISOString() : null,
    valid_until: validUntil ? new Date(validUntil).toISOString() : null,
    max_uses: maxUses,
    max_uses_per_user: maxUsesPerUser,
    allowed_plan_ids: allowedPlansRaw ? allowedPlansRaw.split(",").map(s => s.trim().toLowerCase()).filter(Boolean) : null,
    allowed_course_ids: allowedCoursesRaw ? allowedCoursesRaw.split(",").map(s => s.trim().toLowerCase()).filter(Boolean) : null,
    new_users_only: newUsersOnly,
    active
  };

  const btn = $("#savePromoBtn");
  if (btn) btn.disabled = true;

  try {
    if (id) {
      await api(`/api/studio/promos/${id}`, { method: "PUT", body: JSON.stringify(payload) });
      notice("Промокод обновлён");
    } else {
      await api("/api/studio/promos", { method: "POST", body: JSON.stringify(payload) });
      notice("Промокод создан");
    }
    $("#promoDialog")?.close();
    await loadPromos();
  } catch (err) {
    showErrors(err.message || "Не удалось сохранить промокод");
  } finally {
    if (btn) btn.disabled = false;
  }
}


function boot() {
  // ── Global dialog cancel / backdrop close ──────────────────────────
  document.querySelectorAll("dialog").forEach(function(dlg) {
    dlg.querySelectorAll(".cancel-btn").forEach(function(btn) {
      btn.addEventListener("click", function() { dlg.close(); });
    });
    dlg.addEventListener("click", function(e) {
      if (e.target === dlg) dlg.close();
    });
  });
  // ────────────────────────────────────────────────────────────────────

  bind("loginButton", login);
  // Promo code bindings
  bind("newPromoBtn", () => openPromoDialog());
  bind("promoForm", savePromo, "submit");
  bind("promoBenefitTypeSelect", (e) => {
    const durationRow = $("#promoDurationRow");
    if (durationRow) durationRow.style.display = e.target.value === "N_PERIODS_DISCOUNT" ? "block" : "none";
  }, "change");

  bind("ownerPassword", ev => { if (ev.key === "Enter") login(); }, "keydown");
  bind("logoutButton", async () => { await api("/api/studio/auth/logout", {method:"POST"}); state.csrf=""; releaseBlobs(); showLogin(); });

  // Hierarchy & Course events
  bind("refreshHierarchy", async () => { await loadCourses(); await loadLessons(); });
  bind("hierarchySearch", renderHierarchyTree, "input");
  bind("mediaSearch", renderMediaLibrary, "input");
  bind("mediaFilter", renderMediaLibrary, "change");
  bind("newCourseBtn", () => openCourseDialog("create"));
  bind("courseCoverFile", ev => {
    const file = ev.target.files?.[0];
    if (!file) {
      renderCourseCoverPreview(state.courses.find(course => course.id === state.targetCourseId));
      return;
    }
    ++state.courseCoverPreviewRequest;
    replaceCourseCoverPreview(document.createTextNode("Загружаем новую обложку…"));
    state.courseCoverPreviewUrl = URL.createObjectURL(file);
    const preview = $("#courseCoverPreview");
    if (preview) preview.replaceChildren(courseCoverImage(state.courseCoverPreviewUrl, `Новая обложка: ${file.name}`));
  }, "change");
  bind("confirmCourseAction", confirmCourseAction);
  bind("moveLessonBtn", () => openMoveLessonDialog(state.lessonId));
  bind("confirmMoveLesson", confirmMoveLesson);

  // Tabs
  bind("tabLessonBtn", () => switchEditorTab("lesson"));
  bind("tabHomeworkBtn", () => switchEditorTab("homework"));

  // Lesson events
  bind("newLessonButton", () => openLessonDialog("create"));
  bind("confirmLessonAction", confirmLessonAction);
  ["lessonTitle", "lessonTargetLanguage", "lessonExplanationLanguage", "lessonDifficulty", "lessonMaxRuns", "lessonAccessMode", "lessonMinAge", "lessonCourseSelect", "lessonDescription"].forEach(id => bind(id, markDirty, "input"));
  bind("addSlideButton", () => openAddDialog("slide"));
  bind("addExerciseButton", () => openAddDialog("exercise"));
  bind("addVideoButton", () => openAddDialog("video"));
  bind("confirmAddStep", confirmAdd);
  bind("saveButton", save);
  bind("publishButton", publish);
  bind("previewButton", preview);
  bind("restoreButton", showVersions);
  bind("duplicateLessonButton", () => openLessonDialog("duplicate"));
  bind("toggleLessonButton", toggleLesson);
  bind("deleteLessonButton", () => deleteLesson());

  // Homework Builder events
  ["hwTitle", "hwAvailablePolicy", "hwOptional", "hwDuration", "hwDescription"].forEach(id => bind(id, markHwDirty, "input"));
  bind("hwEnabled", markHwDirty, "change");
  bind("hwBlocksNextLesson", markHwDirty, "change");

  bind("addHwDrawBtn", () => addHwStep("drawing"));
  bind("addHwVoiceBtn", () => addHwStep("voice_answer"));
  bind("addHwChoiceBtn", () => addHwStep("choice"));
  bind("addHwVideoBtn", () => addHwStep("video"));
  bind("addHwInfoBtn", () => addHwStep("presentation"));

  bind("saveHwBtn", saveHomework);
  bind("publishHwBtn", publishHomework);
  bind("duplicateHwBtn", duplicateHomework);
  bind("deleteHwBtn", deleteHomework);
  bind("moveHwBtn", openMoveHomeworkDialog);
  bind("confirmMoveHomework", confirmMoveHomework);

  // Users events
  bind("userSearchBtn", () => loadUsers(($("#userSearch")?.value || "").trim()));
  bind("userSearch", ev => { if (ev.key === "Enter") loadUsers(ev.target.value.trim()); }, "keydown");
  bind("applyOwnerBtn", applyOwnerAllowlist);
  bind("confirmSetRole", applyRole);

  window.addEventListener("beforeunload", ev => {
    if (state.dirty || state.hwDirty) { ev.preventDefault(); ev.returnValue = ""; }
  });

  sessionStorage.removeItem("dome_studio_token");
  // Never consume credentials from URLs. Remove legacy query strings from history.
  if (new URLSearchParams(window.location.search).has("token")) history.replaceState(null,"",window.location.pathname);
  login(true);
}

// Ensure boot is always executed
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => {
    $$(".nav-btn").forEach(b => b.addEventListener("click", () => switchSection(b.dataset.section)));
    boot();
  });
} else {
  $$(".nav-btn").forEach(b => b.addEventListener("click", () => switchSection(b.dataset.section)));
  boot();
}

/* ============================================================================
   CLIENTS CRM — loadClients, openClientCard, renderClientTable
   ========================================================================== */

const STATUS_COLOR = {
  ACTIVE: "#2ecc40", TRIAL: "#7df3e1", REGISTERED: "#aaa",
  EMAIL_NOT_VERIFIED: "#f4a", VERIFIED: "#7df3e1",
  PAST_DUE: "#ff851b", PAYMENT_FAILED: "#ff4136", CANCELLED: "#999",
  EXPIRED: "#888", OWNER: "#ffd700", BLOCKED: "#ff4136", PENDING_APPROVAL: "#ffb020"
};

const PLAN_TITLE = {
  weekly1: "DOME Start", weekly2: "DOME Smart", weekly3: "DOME Plus", weekly4: "DOME Max",
  start: "DOME Start", smart: "DOME Smart", plus: "DOME Plus", max: "DOME Max"
};

async function loadClients() {
  const q = $("#clientSearch")?.value?.trim() || "";
  const plan = $("#clientPlanFilter")?.value || "";
  const period = $("#clientPeriodFilter")?.value || "";
  const status = $("#clientStatusFilter")?.value || "";
  const country = $("#clientCountryFilter")?.value || "";
  const tbody = $("#clientTableBody");
  if (tbody) tbody.innerHTML = `<tr><td colspan="14" style="padding:20px;text-align:center;color:#888">Загрузка...</td></tr>`;

  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (plan) params.set("plan", plan);
  if (period) params.set("period", period);
  if (status) params.set("status", status);
  if (country) params.set("country", country);

  try {
    const data = await api(`/api/studio/admin/clients?${params}`);
    renderClientTable(data.clients || []);
    const cnt = $("#clientCount");
    if (cnt) cnt.textContent = `Найдено: ${data.count || 0} клиентов`;
  } catch(e) {
    if (tbody) tbody.innerHTML = `<tr><td colspan="14" style="padding:20px;text-align:center;color:#f44">${escH(e.message)}</td></tr>`;
  }
}

function renderClientTable(clients) {
  const tbody = $("#clientTableBody");
  if (!tbody) return;
  if (!clients.length) {
    tbody.innerHTML = `<tr><td colspan="14" style="padding:20px;text-align:center;color:#888">Клиенты не найдены</td></tr>`;
    return;
  }
  tbody.innerHTML = clients.map(c => {
    const sub = c.subscription || {};
    const statusColor = STATUS_COLOR[c.status] || "#aaa";
    const planTitle = PLAN_TITLE[sub.plan_id] || sub.plan_id || "—";
    const periodLabel = sub.billing_period === "YEAR" ? "Год" : (sub.billing_period === "MONTH" ? "Мес" : "—");
    const price = sub.price ? `€${sub.price}` : "—";
    const lessonsLabel = sub.status ? `${sub.lessons_used || 0}/${(sub.lessons_used||0)+(sub.lessons_remaining||0)}` : "—";
    const regDate = c.registered_at ? c.registered_at.split("T")[0] : "—";
    const lastActive = c.last_active_at ? c.last_active_at.replace("T", " ").slice(0, 16) : "—";
    const childrenCount = (c.children || []).length;
    const ownerBadge = c.is_owner ? ' <span style="background:#ffd700;color:#000;font-size:10px;padding:1px 5px;border-radius:4px">OWNER</span>' : '';
    const verifiedBadge = c.email_verified ? '✅' : '❌';
    const accessAction = c.is_owner
      ? `<span style="color:#ffd700;font-size:12px;font-weight:700">UNLIMITED</span>`
      : c.account_status === "BLOCKED"
        ? `<button class="primary" style="padding:3px 8px;font-size:12px" onclick="event.stopPropagation();setClientAccess(${c.id},'ACTIVE')">Разблокировать</button>`
        : c.account_status === "PENDING_APPROVAL"
          ? `<button class="primary" style="padding:3px 8px;font-size:12px" onclick="event.stopPropagation();setClientAccess(${c.id},'ACTIVE')">Разрешить</button>`
          : `<button style="padding:3px 8px;font-size:12px;color:#ffb4b4" onclick="event.stopPropagation();setClientAccess(${c.id},'BLOCKED')">Заблокировать</button>`;
    return `<tr style="border-bottom:1px solid #1a1a2e;cursor:pointer" onclick="openClientCard(${c.id})">
      <td style="padding:7px 8px;color:#888">${c.id}</td>
      <td style="padding:7px 8px">${escH(c.display_name)}${ownerBadge}</td>
      <td style="padding:7px 8px;color:#7df3e1">${escH(c.email)}</td>
      <td style="padding:7px 8px;text-align:center">${verifiedBadge}</td>
      <td style="padding:7px 8px;color:#aaa">${escH(c.country || "—")}</td>
      <td style="padding:7px 8px;color:#888;font-size:12px">${regDate}</td>
      <td style="padding:7px 8px;color:#aaa;font-size:12px">${escH(lastActive)}</td>
      <td style="padding:7px 8px;text-align:center">${childrenCount}</td>
      <td style="padding:7px 8px">${escH(planTitle)}</td>
      <td style="padding:7px 8px;color:#aaa">${periodLabel}</td>
      <td style="padding:7px 8px">${price}</td>
      <td style="padding:7px 8px"><span style="color:${statusColor};font-weight:600;font-size:12px">${c.status}</span></td>
      <td style="padding:7px 8px;color:#aaa">${lessonsLabel}</td>
      <td style="padding:7px 8px;white-space:nowrap"><button class="primary" style="padding:3px 8px;font-size:12px" onclick="event.stopPropagation();openClientCard(${c.id})">Открыть</button> ${accessAction}</td>
    </tr>`;
  }).join("");
}

let _clientCard = null;

async function openClientCard(parentId) {
  const modal = $("#clientCardModal");
  if (!modal) return;
  modal.classList.remove("hidden");
  const title = $("#clientCardTitle");
  if (title) title.textContent = "⏳ Загрузка...";
  clearClientCardTabs();

  try {
    const data = await api(`/api/studio/admin/clients/${parentId}`);
    _clientCard = data;
    const p = data.parent || {};
    const ownerLabel = p.is_owner ? ' 👑 OWNER' : '';
    if (title) title.textContent = `👤 ${p.display_name || p.email}${ownerLabel} (ID: ${p.id})`;
    renderClientCardTab("profile");
  } catch(e) {
    if (title) title.textContent = "❌ Ошибка загрузки";
    $("#clientTabProfile").innerHTML = `<p style="color:#f44">${escH(e.message)}</p>`;
  }
}

function clearClientCardTabs() {
  [$("#clientTabProfile"), $("#clientTabChildren"), $("#clientTabSubscription"),
   $("#clientTabLessons"), $("#clientTabPayments"), $("#clientTabPromos"),
   $("#clientTabConsents"), $("#clientTabAudit")].forEach(el => { if (el) el.innerHTML = ""; });
}

function renderClientCardTab(tab) {
  $$(".client-tab-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
  const panels = { profile: "#clientTabProfile", children: "#clientTabChildren", subscription: "#clientTabSubscription",
    lessons: "#clientTabLessons", payments: "#clientTabPayments", promos: "#clientTabPromos",
    consents: "#clientTabConsents", audit: "#clientTabAudit" };
  Object.entries(panels).forEach(([t, sel]) => {
    const el = $(sel);
    if (el) el.classList.toggle("hidden", t !== tab);
  });

  const d = _clientCard;
  if (!d) return;

  if (tab === "profile") {
    const p = d.parent;
    const ownerBadge = p.is_owner ? `<span style="background:#ffd700;color:#000;padding:2px 8px;border-radius:4px;font-weight:700">👑 OWNER</span>` : "";
    $("#clientTabProfile").innerHTML = `
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;font-size:14px">
        <div><strong>Имя:</strong> ${escH(p.first_name)} ${escH(p.last_name)}</div>
        <div><strong>Email:</strong> <a style="color:#7df3e1" href="mailto:${escH(p.email)}">${escH(p.email)}</a> ${p.email_verified ? "✅" : "❌"}</div>
        <div><strong>Телефон:</strong> ${escH(p.phone || "—")}</div>
        <div><strong>Страна:</strong> ${escH(p.country || "—")}</div>
        <div><strong>Язык:</strong> ${escH(p.preferred_language || "ru")}</div>
        <div><strong>Роль:</strong> ${ownerBadge || escH(p.account_role)}</div>
        <div><strong>Доступ:</strong> <span style="color:${STATUS_COLOR[p.account_status] || '#aaa'};font-weight:700">${escH(p.is_owner ? 'OWNER / UNLIMITED' : (p.account_status || 'ACTIVE'))}</span></div>
        <div><strong>Регистрация:</strong> ${(p.registered_at || "").split("T")[0] || "—"}</div>
        <div><strong>Последняя активность:</strong> ${(p.last_active_at || "").replace("T", " ").slice(0, 16) || "—"}</div>
        <div><strong>Рассылка:</strong> ${p.marketing_opt_in ? "✅ Да" : "❌ Нет"}</div>
        <div><strong>Онбординг:</strong> ${escH(p.onboarding_stage)}</div>
        <div><strong>Верификация:</strong> ${escH(p.verification_status)}</div>
      </div>
      ${p.is_owner ? `<p style="color:#ffd700;font-weight:700">OWNER / UNLIMITED — блокировка недоступна.</p>` : `<div style="display:flex;gap:8px;margin-top:16px"><button class="primary" onclick="setClientAccess(${p.id},'ACTIVE')">Разрешить / разблокировать</button><button style="color:#ffb4b4" onclick="setClientAccess(${p.id},'BLOCKED')">Заблокировать</button></div>`}`;
  } else if (tab === "children") {
    const items = (d.children || []).map(c => `
      <div style="background:#1a1a2e;border-radius:8px;padding:12px;margin-bottom:8px">
        <strong>${escH(c.name)}</strong> (${c.age} лет) — ${escH(c.native_language)} → ${escH(c.target_language)}<br>
        <small>Уровень: ${escH(c.level)} | ID: ${c.id}</small>
      </div>`).join("") || "<p style='color:#888'>Дети не добавлены</p>";
    $("#clientTabChildren").innerHTML = items;
  } else if (tab === "subscription") {
    const subs = d.subscriptions || [];
    if (!subs.length) { $("#clientTabSubscription").innerHTML = "<p style='color:#888'>Подписок нет</p>"; return; }
    $("#clientTabSubscription").innerHTML = subs.map(s => {
      const planTitle = PLAN_TITLE[s.plan_id] || s.plan_id || "—";
      const statusColor = STATUS_COLOR[s.status] || "#aaa";
      const isAnnual = String(s.billing_period || "").toUpperCase() === "YEAR";
      const billingLabel = isAnnual ? "Годовая" : "Ежемесячная";
      const lessonsPerMonth = (s.lessons_per_week || 1) * 4;
      const priceVal = s.current_plan_price || (isAnnual ? 399 : (s.monthly_price || 39));
      const priceText = isAnnual ? `€${priceVal} / год` : `€${priceVal} / месяц`;
      const equivText = isAnnual ? `<br><strong>Эквивалент:</strong> ≈ €${(priceVal / 12).toFixed(2)} / месяц` : "";
      const nextCharge = s.next_charge_at ? s.next_charge_at.split("T")[0] : "—";
      return `<div style="background:#1a1a2e;border-radius:8px;padding:14px;margin-bottom:10px;font-size:13px;line-height:1.6;border:1px solid #334155">
        <div><strong>Тариф:</strong> <span style="color:#7df3e1;font-weight:700;font-size:15px">${escH(planTitle)}</span> — <span style="color:${statusColor};font-weight:700">${s.status}</span></div>
        <div><strong>Уроков:</strong> ${lessonsPerMonth} / месяц</div>
        <div><strong>Оплата:</strong> ${billingLabel}</div>
        <div><strong>Стоимость:</strong> ${priceText}${equivText}</div>
        <div><strong>Уроков использовано:</strong> ${s.lessons_used || 0} (из начисленных ${s.lessons_allocated || 0})</div>
        <div><strong>Дата старта:</strong> ${(s.started_at || "").split("T")[0] || "—"}</div>
        <div><strong>Следующее продление:</strong> <span style="color:#7df3e1">${nextCharge}</span></div>
        <div><strong>Провайдер:</strong> ${s.payment_provider || "—"} | <strong>Sub ID:</strong> <span style="color:#aaa">${s.provider_subscription_id || "—"}</span></div>
        ${s.ended_at ? `<div style="color:#f44;margin-top:4px"><strong>Отменено:</strong> ${s.ended_at.split("T")[0]}</div>` : ""}
      </div>`;
    }).join("");
  } else if (tab === "lessons") {
    const sessions = d.lessons || [];
    if (!sessions.length) { $("#clientTabLessons").innerHTML = "<p style='color:#888'>Уроков нет</p>"; return; }
    $("#clientTabLessons").innerHTML = `<table style="width:100%;border-collapse:collapse;font-size:13px">
      <thead><tr style="background:#1a1a2e;color:#7df3e1"><th style="padding:6px">Урок</th><th>Статус</th><th>Начало</th><th>Завершён</th></tr></thead>
      <tbody>${sessions.map(s => `<tr style="border-bottom:1px solid #1a1a2e">
        <td style="padding:6px">${escH(s.lesson_id)}</td>
        <td style="padding:6px">${escH(s.status)}</td>
        <td style="padding:6px">${(s.started_at || "").split("T")[0] || "—"}</td>
        <td style="padding:6px">${(s.completed_at || "").split("T")[0] || "—"}</td>
      </tr>`).join("")}</tbody>
    </table>`;
  } else if (tab === "payments") {
    const payments = d.payments || [];
    if (!payments.length) { $("#clientTabPayments").innerHTML = "<p style='color:#888'>Платежей нет</p>"; return; }
    $("#clientTabPayments").innerHTML = `<table style="width:100%;border-collapse:collapse;font-size:13px">
      <thead><tr style="background:#1a1a2e;color:#7df3e1"><th style="padding:6px">ID</th><th>Провайдер</th><th>Событие</th><th>Дата</th></tr></thead>
      <tbody>${payments.map(p => `<tr style="border-bottom:1px solid #1a1a2e">
        <td style="padding:6px">${p.id}</td>
        <td style="padding:6px">${escH(p.provider)}</td>
        <td style="padding:6px">${escH(p.event_type)}</td>
        <td style="padding:6px">${(p.processed_at || "").split("T")[0] || "—"}</td>
      </tr>`).join("")}</tbody>
    </table>`;
  } else if (tab === "promos") {
    const promos = d.promos || [];
    if (!promos.length) { $("#clientTabPromos").innerHTML = "<p style='color:#888'>Промокоды не использовались</p>"; return; }
    $("#clientTabPromos").innerHTML = promos.map(pr => `
      <div style="background:#1a1a2e;border-radius:8px;padding:10px;margin-bottom:8px;font-size:13px">
        Промокод: <strong>${escH(pr.payment_reference || "—")}</strong><br>
        Скидка: €${pr.discount_amount || 0} | Финал: €${pr.final_price || 0} (было €${pr.original_price || 0})<br>
        Использован: ${(pr.used_at || "").split("T")[0] || "—"}
      </div>`).join("");
  } else if (tab === "consents") {
    const consents = d.consents || [];
    if (!consents.length) { $("#clientTabConsents").innerHTML = "<p style='color:#888'>Документы не подписаны</p>"; return; }
    $("#clientTabConsents").innerHTML = `<table style="width:100%;border-collapse:collapse;font-size:13px">
      <thead><tr style="background:#1a1a2e;color:#7df3e1"><th style="padding:6px">Документ</th><th>Версия</th><th>Принят</th><th>Дата</th></tr></thead>
      <tbody>${consents.map(c => `<tr style="border-bottom:1px solid #1a1a2e">
        <td style="padding:6px">${escH(c.title)}</td>
        <td style="padding:6px">${escH(c.document_version)}</td>
        <td style="padding:6px;text-align:center">${c.accepted ? "✅" : "❌"}</td>
        <td style="padding:6px">${(c.accepted_at || "").split("T")[0] || "—"}</td>
      </tr>`).join("")}</tbody>
    </table>`;
  } else if (tab === "audit") {
    const audits = d.audit || [];
    if (!audits.length) { $("#clientTabAudit").innerHTML = "<p style='color:#888'>История пуста</p>"; return; }
    $("#clientTabAudit").innerHTML = `<table style="width:100%;border-collapse:collapse;font-size:13px">
      <thead><tr style="background:#1a1a2e;color:#7df3e1"><th style="padding:6px">Действие</th><th>Актор</th><th>Дата</th></tr></thead>
      <tbody>${audits.map(a => `<tr style="border-bottom:1px solid #1a1a2e">
        <td style="padding:6px">${escH(a.action)}</td>
        <td style="padding:6px">${escH(a.actor)}</td>
        <td style="padding:6px">${(a.created_at || "").split("T")[0] || "—"}</td>
      </tr>`).join("")}</tbody>
    </table>`;
  }
}

async function loadNewUserAccessMode() {
  const select = $("#newUserAccessMode");
  if (!select) return;
  try {
    const data = await api("/api/studio/admin/access-settings");
    select.value = data.new_user_access_mode === "MANUAL" ? "MANUAL" : "AUTOMATIC";
  } catch (error) {
    console.warn("ACCOUNT_ACCESS_SETTINGS_LOAD_FAILED", error);
  }
}

async function saveNewUserAccessMode() {
  const select = $("#newUserAccessMode");
  if (!select) return;
  const value = select.value === "MANUAL" ? "MANUAL" : "AUTOMATIC";
  try {
    await api("/api/studio/admin/access-settings", { method: "PUT", body: JSON.stringify({ new_user_access_mode: value }) });
    notice(value === "MANUAL" ? "Новые пользователи будут ждать одобрения." : "Новые пользователи будут получать доступ автоматически.");
  } catch (error) {
    alert("Не удалось сохранить режим доступа: " + error.message);
    await loadNewUserAccessMode();
  }
}

async function setClientAccess(parentId, status) {
  const label = status === "BLOCKED" ? "заблокировать" : "разблокировать";
  if (status === "BLOCKED" && !confirm(`Вы уверены, что хотите ${label} пользователя? Его профиль и прогресс сохранятся.`)) return;
  try {
    await api(`/api/studio/admin/clients/${parentId}/access`, { method: "POST", body: JSON.stringify({ status }) });
    notice(status === "BLOCKED" ? "Доступ пользователя приостановлен." : "Доступ пользователя активен.");
    await loadClients();
    if (_clientCard?.parent?.id === parentId) await openClientCard(parentId);
  } catch (error) {
    alert("Не удалось изменить доступ: " + error.message);
  }
}

/* ============================================================================
   TARIFF MANAGEMENT
   ========================================================================== */

async function loadTariffs() {
  const grid = $("#tariffGrid");
  if (grid) grid.innerHTML = `<div style="color:#888;padding:20px">Загрузка тарифов...</div>`;
  try {
    const data = await api("/api/studio/admin/tariffs");
    renderTariffGrid(data.tariffs || []);
  } catch(e) {
    if (grid) grid.innerHTML = `<div style="color:#f44;padding:20px">${escH(e.message)}</div>`;
  }
}

function renderTariffGrid(tariffs) {
  const grid = $("#tariffGrid");
  if (!grid) return;
  grid.innerHTML = tariffs.map(t => {
    const savingsLabel = t.yearly_savings > 0 ? `<small style="color:#2ecc40">Выгода €${t.yearly_savings}/год</small>` : "";
    return `<div style="background:#1a1a2e;border-radius:12px;padding:16px;border:1px solid ${t.active ? '#7df3e1' : '#333'}">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
        <strong style="font-size:16px;color:#7df3e1">${escH(t.name)}</strong>
        <span style="font-size:11px;color:${t.active ? '#2ecc40' : '#888'}">${t.active ? "✅ Активен" : "❌ Скрыт"}</span>
      </div>
      <div style="font-size:13px;margin-bottom:8px">
        ${t.lessons_per_month} уроков/мес | ${t.lessons_per_year} уроков/год<br>
        <strong style="font-size:18px;color:#fff">€${t.monthly_price}</strong>/мес<br>
        <strong style="font-size:15px;color:#7df3e1">€${t.annual_price}</strong>/год (€${t.annual_as_monthly}/мес)<br>
        ${savingsLabel}
      </div>
      <div style="margin-top:12px;display:flex;flex-direction:column;gap:8px">
        <label style="font-size:12px;color:#aaa">Название<input type="text" value="${escH(t.name)}" id="tname_${t.plan_id}" style="margin-top:4px;width:100%"></label>
        <label style="font-size:12px;color:#aaa">Описание<input type="text" value="${escH(t.description || "")}" id="tdesc_${t.plan_id}" style="margin-top:4px;width:100%"></label>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
          <label style="font-size:12px;color:#aaa">€/мес<input type="number" step="0.01" value="${t.monthly_price}" id="tmonth_${t.plan_id}" style="margin-top:4px;width:100%"></label>
          <label style="font-size:12px;color:#aaa">€/год<input type="number" step="0.01" value="${t.annual_price}" id="tyear_${t.plan_id}" style="margin-top:4px;width:100%"></label>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
          <label style="font-size:12px;color:#aaa">Уроков/месяц<input type="number" min="4" step="4" value="${t.lessons_per_month}" id="tlessons_${t.plan_id}" style="margin-top:4px;width:100%"></label>
          <label style="font-size:12px;color:#aaa">Порядок<input type="number" min="1" value="${t.display_order}" id="torder_${t.plan_id}" style="margin-top:4px;width:100%"></label>
        </div>
        <small style="color:#94a3b8">Изменение цены создаёт новую версию только для новых подписок. Цена действующих подписчиков не меняется.</small>
        <label style="font-size:12px"><input type="checkbox" id="tactive_${t.plan_id}" ${t.active ? "checked" : ""}> Активен / Видим</label>
        <button class="primary" style="width:100%" onclick="saveTariff('${t.plan_id}')">💾 Сохранить тариф</button>
      </div>
    </div>`;
  }).join("");
}

async function saveTariff(planId) {
  const name = $(`#tname_${planId}`)?.value?.trim();
  const monthlyPrice = parseFloat($(`#tmonth_${planId}`)?.value || "0");
  const annualPrice = parseFloat($(`#tyear_${planId}`)?.value || "0");
  const description = $(`#tdesc_${planId}`)?.value?.trim() || "";
  const lessonsPerMonth = parseInt($(`#tlessons_${planId}`)?.value || "0", 10);
  const displayOrder = parseInt($(`#torder_${planId}`)?.value || "0", 10);
  const active = $(`#tactive_${planId}`)?.checked;
  if (!name || !monthlyPrice || !annualPrice || !lessonsPerMonth || !displayOrder) { alert("Заполните все поля тарифа"); return; }
  try {
    await api(`/api/studio/admin/tariffs/${planId}`, {
      method: "PUT",
      body: JSON.stringify({ name, description, monthly_price: monthlyPrice, annual_price: annualPrice, lessons_per_month: lessonsPerMonth, display_order: displayOrder, active, visible: active })
    });
    notice("✅ Тариф обновлён успешно!");
    await loadTariffs();
  } catch(e) {
    alert("Ошибка: " + e.message);
  }
}

/* ============================================================================
   CLIENTS EVENT WIRING (runs after DOM is ready)
   ========================================================================== */

document.addEventListener("DOMContentLoaded", () => {
  // Client search & filters
  $("#clientSearchBtn")?.addEventListener("click", loadClients);
  $("#clientSearch")?.addEventListener("keydown", e => { if (e.key === "Enter") loadClients(); });
  $("#clientPlanFilter")?.addEventListener("change", loadClients);
  $("#clientPeriodFilter")?.addEventListener("change", loadClients);
  $("#clientStatusFilter")?.addEventListener("change", loadClients);
  $("#clientCountryFilter")?.addEventListener("change", loadClients);
  $("#newUserAccessMode")?.addEventListener("change", saveNewUserAccessMode);
  loadNewUserAccessMode();

  // CSV export
  $("#clientExportCsvBtn")?.addEventListener("click", () => {
    const q = $("#clientSearch")?.value?.trim() || "";
    const plan = $("#clientPlanFilter")?.value || "";
    const period = $("#clientPeriodFilter")?.value || "";
    const status = $("#clientStatusFilter")?.value || "";
    const params = new URLSearchParams({ export: "csv" });
    if (q) params.set("q", q);
    if (plan) params.set("plan", plan);
    if (period) params.set("period", period);
    if (status) params.set("status", status);
    const url = `/api/studio/admin/clients?${params}`;
    const a = document.createElement("a");
    a.href = url;
    a.download = "dome_clients.csv";
    a.click();
  });

  // Client card modal tabs
  $$(".client-tab-btn").forEach(btn => {
    btn.addEventListener("click", () => renderClientCardTab(btn.dataset.tab));
  });

  // Client card close
  $("#clientCardClose")?.addEventListener("click", () => {
    $("#clientCardModal")?.classList.add("hidden");
    _clientCard = null;
  });
  $("#clientCardModal")?.addEventListener("click", e => {
    if (e.target === $("#clientCardModal")) {
      $("#clientCardModal")?.classList.add("hidden");
      _clientCard = null;
    }
  });

  // Tariff refresh
  $("#tariffRefreshBtn")?.addEventListener("click", loadTariffs);
  // Media Manager Listeners
  $("#mediaUploadInput")?.addEventListener("change", e => uploadMediaFiles(e.target.files));
  $("#mediaRefreshBtn")?.addEventListener("click", loadMediaManager);
  $("#mediaSearch")?.addEventListener("input", renderMediaGrid);
  $("#mediaTypeFilter")?.addEventListener("change", renderMediaGrid);

});




/* ============================================================================
   DOME PRODUCTION CMS — CLIENT-SIDE MODULES
   ========================================================================== */

let _allMediaFiles = [];
let _currentMovieConfig = { scenes: [] };
let _currentMovieLessonId = "";

/* --- 1. MEDIA MANAGER --- */
async function loadMediaManager() {
  try {
    const data = await api("/api/studio/cms/media");
    _allMediaFiles = data.files || [];
    renderMediaGrid();
  } catch(e) {
    console.warn("Failed to load media:", e);
    const grid = $("#mediaGrid");
    if (grid) grid.innerHTML = `<div class="card" style="grid-column:1/-1;color:var(--danger)">Ошибка загрузки медиатеки: ${escH(e.message)}</div>`;
  }
}

function renderMediaGrid() {
  const grid = $("#mediaGrid");
  if (!grid) return;
  const q = ($("#mediaSearch")?.value || "").trim().toLowerCase();
  const typeFilter = $("#mediaTypeFilter")?.value || "";

  const filtered = _allMediaFiles.filter(f => {
    if (typeFilter && f.type !== typeFilter) return false;
    if (q && !f.name.toLowerCase().includes(q) && !(f.path || "").toLowerCase().includes(q)) return false;
    return true;
  });

  if (filtered.length === 0) {
    grid.innerHTML = '<div class="card" style="grid-column:1/-1;text-align:center;color:var(--muted);padding:30px">Файлы не найдены</div>';
    return;
  }

  grid.innerHTML = filtered.map(f => {
    const isImg = f.type === "image";
    const isAudio = f.type === "audio";
    const isVideo = f.type === "video";
    const usedIn = f.used_in || [];
    const usedBadge = usedIn.length > 0
      ? `<span style="background:#DCFCE7;color:#15803D;padding:2px 6px;border-radius:4px;font-size:11px;font-weight:700">Используется (${usedIn.length})</span>`
      : `<span style="background:#F1F5F9;color:#64748B;padding:2px 6px;border-radius:4px;font-size:11px">Не используется</span>`;

    const usedTooltip = usedIn.length > 0 ? `title="${escH(usedIn.join(', '))}"` : '';

    return `<div class="card" style="display:flex;flex-direction:column;gap:8px;padding:12px">
      <div style="height:120px;background:#0F172A;border-radius:6px;display:flex;align-items:center;justify-content:center;overflow:hidden">
        ${isImg ? `<img src="${escH(f.url)}" style="max-height:100%;max-width:100%;object-fit:contain" alt="${escH(f.name)}">` : ''}
        ${isAudio ? `<div style="text-align:center;color:#38BDF8"><div style="font-size:32px">🎵</div><audio controls src="${escH(f.url)}" style="width:180px;height:32px;margin-top:6px"></audio></div>` : ''}
        ${isVideo ? `<div style="text-align:center;color:#F59E0B"><div style="font-size:32px">🎬</div><span style="font-size:11px;color:#cbd5e1">Видеофайл</span></div>` : ''}
        ${(!isImg && !isAudio && !isVideo) ? `<div style="font-size:32px;color:#94A3B8">📄</div>` : ''}
      </div>
      <div style="font-weight:600;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${escH(f.name)}">${escH(f.name)}</div>
      <div style="display:flex;justify-content:space-between;align-items:center;font-size:11px;color:var(--muted)">
        <span>${f.size_formatted || (Math.round((f.size||0)/1024)+' KB')}</span>
        <span ${usedTooltip}>${usedBadge}</span>
      </div>
      <div style="display:flex;gap:6px;margin-top:4px">
        <a href="${escH(f.url)}" target="_blank" class="primary small-btn" style="text-align:center;text-decoration:none;flex:1;padding:4px 8px">Открыть</a>
        <button class="danger small-btn" style="padding:4px 8px" onclick="deleteMediaFile('${escH(f.path)}', ${usedIn.length})">Удалить</button>
      </div>
    </div>`;
  }).join("");
}

async function uploadMediaFiles(fileList) {
  if (!fileList || fileList.length === 0) return;
  notice(`Загрузка ${fileList.length} файлов...`);
  try {
    for (const file of fileList) {
      const formData = new FormData();
      formData.append("file", file);
      await api("/api/studio/cms/media", { method: "POST", body: formData });
    }
    notice("✅ Файлы успешно загружены в медиатеку!");
    await loadMediaManager();
  } catch(e) {
    alert("Ошибка загрузки: " + e.message);
  }
}

async function deleteMediaFile(path, usedCount) {
  if (usedCount > 0) {
    const confirmDelete = confirm(`⚠️ Внимание: этот файл используется в ${usedCount} местах (курсы/уроки). Удаление может сломать отображение. Всё равно удалить?`);
    if (!confirmDelete) return;
  } else {
    if (!confirm("Удалить выбранный медиафайл?")) return;
  }
  try {
    await api(`/api/studio/cms/media?path=${encodeURIComponent(path)}`, { method: "DELETE" });
    notice("✅ Файл удалён");
    await loadMediaManager();
  } catch(e) {
    alert("Ошибка удаления: " + e.message);
  }
}


/* --- 2. HEROES MANAGER --- */
function switchHeroSubTab(tab) {
  if (tab === 'presets') {
    $("#heroSubPresets")?.classList.remove("hidden");
    $("#heroSubCustom")?.classList.add("hidden");
    $("#heroTabPresetsBtn")?.classList.add("active");
    $("#heroTabCustomBtn")?.classList.remove("active");
  } else {
    $("#heroSubPresets")?.classList.add("hidden");
    $("#heroSubCustom")?.classList.remove("hidden");
    $("#heroTabPresetsBtn")?.classList.remove("active");
    $("#heroTabCustomBtn")?.classList.add("active");
    loadCustomHeroes();
  }
}

async function loadHeroManager() {
  try {
    const data = await api("/api/studio/cms/heroes");
    const heroes = data.heroes || [];
    renderPresetHeroes(heroes);
  } catch(e) {
    console.warn("Failed to load heroes:", e);
  }
}

function renderPresetHeroes(heroes) {
  const grid = $("#presetHeroesGrid");
  if (!grid) return;
  grid.innerHTML = heroes.map(h => {
    const isBothCatsNote = (h.id === "cat" || h.id === "dome_cat")
      ? `<div style="font-size:10px;color:#1D4ED8;background:#EFF6FF;padding:2px 4px;border-radius:4px;margin-top:2px">Персонаж кота: ${h.id === 'cat' ? 'Old Gray Cat' : 'New DOME Cat (худи)'}</div>`
      : '';

    return `<div class="card" style="display:flex;flex-direction:column;gap:8px;padding:14px">
      <div style="height:120px;background:#F8FAFC;border:1px solid #E2E8F0;border-radius:8px;display:flex;align-items:center;justify-content:center;overflow:hidden">
        <img src="/content-studio/heroes/${escH(h.asset_file)}" style="max-height:100%;max-width:100%;object-fit:contain" alt="${escH(h.name)}" onerror="this.src='/content-studio/heroes/all_characters.png'">
      </div>
      <div style="display:flex;justify-content:space-between;align-items:flex-start">
        <div>
          <strong style="font-size:15px">${escH(h.name)}</strong>
          <div style="font-size:12px;color:var(--muted)">ID: <code>${escH(h.id)}</code></div>
          ${isBothCatsNote}
        </div>
        <label class="check" style="font-size:12px">
          <input type="checkbox" id="heroActive_${h.id}" ${h.active !== false ? "checked" : ""} onchange="updateHeroStatus('${h.id}', this.checked)"> Активен
        </label>
      </div>
      <p style="font-size:12px;color:var(--muted);margin:2px 0">${escH(h.description || '')}</p>
      <div style="margin-top:auto;padding-top:6px;border-top:1px solid var(--border);display:flex;gap:6px">
        <input id="heroVoice_${h.id}" placeholder="Voice ID" value="${escH(h.voice_id || '')}" style="font-size:12px;padding:4px 6px;flex:1">
        <button class="primary small-btn" onclick="saveHeroVoice('${h.id}')">Сохранить</button>
      </div>
    </div>`;
  }).join("");
}

async function updateHeroStatus(heroId, active) {
  try {
    await api("/api/studio/cms/heroes", {
      method: "POST",
      body: JSON.stringify({ hero_id: heroId, active })
    });
    notice(`✅ Статус героя ${heroId} обновлён`);
  } catch(e) {
    alert("Ошибка: " + e.message);
  }
}

async function saveHeroVoice(heroId) {
  const voiceId = $(`#heroVoice_${heroId}`)?.value?.trim() || "";
  try {
    await api("/api/studio/cms/heroes", {
      method: "POST",
      body: JSON.stringify({ hero_id: heroId, voice_id: voiceId })
    });
    notice(`✅ Голос героя ${heroId} сохранён`);
  } catch(e) {
    alert("Ошибка: " + e.message);
  }
}

async function loadCustomHeroes() {
  const grid = $("#customHeroesGrid");
  if (!grid) return;
  grid.innerHTML = '<div class="card" style="grid-column:1/-1;text-align:center;color:var(--muted)">Загрузка рисунков детей...</div>';
  try {
    const data = await api("/api/studio/cms/custom-heroes");
    const customs = data.custom_heroes || [];
    if (customs.length === 0) {
      grid.innerHTML = '<div class="card" style="grid-column:1/-1;text-align:center;color:var(--muted);padding:24px">Пока нет загруженных детских рисунков</div>';
      return;
    }
    grid.innerHTML = customs.map(c => `
      <div class="card" style="display:flex;flex-direction:column;gap:8px;padding:14px">
        <div style="height:140px;background:#0F172A;border-radius:8px;display:flex;align-items:center;justify-content:center;overflow:hidden">
          ${c.drawing_url ? `<img src="${escH(c.drawing_url)}" style="max-height:100%;max-width:100%;object-fit:contain">` : '<span style="color:#64748B">Нет превью</span>'}
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center">
          <strong>Ребёнок: ID ${c.child_id || c.id}</strong>
          <span style="font-size:11px;padding:2px 6px;border-radius:4px;background:#EBF3FF;color:#246BFD;font-weight:700">${escH(c.visual_analysis_status || c.status || 'NEW')}</span>
        </div>
        <div style="font-size:12px;color:var(--muted)">Источник: <code>${escH(c.source || 'CHILD_DRAWING')}</code></div>
        ${c.error ? `<div style="font-size:11px;color:var(--danger)">${escH(c.error)}</div>` : ''}
        <button class="primary small-btn" style="margin-top:6px" onclick="retryCustomHero('${c.id}')">🔄 Перегенерировать персонажа</button>
      </div>
    `).join("");
  } catch(e) {
    grid.innerHTML = `<div class="card" style="grid-column:1/-1;color:var(--danger)">Ошибка загрузки: ${escH(e.message)}</div>`;
  }
}

async function retryCustomHero(charId) {
  try {
    await api(`/api/studio/cms/custom-heroes/${charId}/retry`, { method: "POST" });
    notice("✅ Запущена повторная обработка детского рисунка");
    await loadCustomHeroes();
  } catch(e) {
    alert("Ошибка: " + e.message);
  }
}


/* --- 3. ANIMATIONS LIBRARY --- */
async function loadAnimationsManager() {
  try {
    const data = await api("/api/studio/cms/animations");
    const anims = data.animations || [];
    renderAnimationsGrid(anims);
  } catch(e) {
    console.warn("Failed to load animations:", e);
  }
}

function renderAnimationsGrid(anims) {
  const grid = $("#animationsGrid");
  if (!grid) return;
  grid.innerHTML = anims.map(a => `
    <div class="card" style="display:flex;flex-direction:column;gap:6px;padding:14px">
      <div style="display:flex;justify-content:space-between;align-items:flex-start">
        <strong>${escH(a.name || a.id)}</strong>
        <label class="check" style="font-size:11px">
          <input type="checkbox" ${a.active !== false ? "checked" : ""} onchange="toggleAnimation('${a.id}', this.checked)"> Вкл
        </label>
      </div>
      <div style="font-size:12px;color:var(--muted)">ID: <code>${escH(a.id)}</code></div>
      <div style="font-size:12px;color:var(--muted)">Длительность: <strong>${a.duration || 2.0}с</strong></div>
      <p style="font-size:12px;color:var(--muted);margin:4px 0">${escH(a.description || '')}</p>
      <div style="margin-top:auto;padding-top:6px;border-top:1px solid var(--border);display:flex;justify-content:space-between;align-items:center">
        <span style="font-size:10px;background:#F1F5F9;color:#64748B;padding:2px 6px;border-radius:4px">Герои: ${(a.compatible_heroes || ['all']).join(', ')}</span>
        <button class="primary small-btn" style="padding:2px 8px;font-size:11px" onclick="previewAnimationActions('${a.id}')">Шаги</button>
      </div>
    </div>
  `).join("");
}

async function toggleAnimation(animId, active) {
  try {
    await api(`/api/studio/cms/animations/${animId}/toggle`, {
      method: "POST",
      body: JSON.stringify({ active })
    });
    notice(`✅ Анимация ${animId} ${active ? 'включена' : 'отключена'}`);
  } catch(e) {
    alert("Ошибка: " + e.message);
  }
}

function openNewAnimationDialog() {
  const dlg = $("#newAnimationDialog");
  if (dlg) dlg.showModal();
}

async function submitNewAnimation() {
  const id = $("#animIdInput")?.value?.trim();
  const name = $("#animNameInput")?.value?.trim();
  const description = $("#animDescInput")?.value?.trim() || "";
  const duration = parseFloat($("#animDurationInput")?.value || "2.5");
  const hero = $("#animHeroSelect")?.value || "all";
  let actions = [];
  try {
    actions = JSON.parse($("#animActionsInput")?.value || "[]");
  } catch {
    alert("Ошибка в формате JSON действий");
    return;
  }

  if (!id || !name) {
    alert("Укажите ID и название движения");
    return;
  }

  try {
    await api("/api/studio/cms/animations", {
      method: "POST",
      body: JSON.stringify({
        id,
        name,
        description,
        duration,
        compatible_heroes: hero === "all" ? ["all"] : [hero],
        actions
      })
    });
    $("#newAnimationDialog")?.close();
    notice(`✅ Новое движение «${name}» добавлено в библиотеку!`);
    await loadAnimationsManager();
  } catch(e) {
    alert("Ошибка добавления анимации: " + e.message);
  }
}

function previewAnimationActions(animId) {
  api("/api/studio/cms/animations").then(data => {
    const a = (data.animations || []).find(x => x.id === animId);
    if (!a) return;
    alert(`Движение: ${a.name} (${a.id})\\n\\nДействия:\\n${JSON.stringify(a.actions || [], null, 2)}`);
  });
}


/* --- 4. MOVIE BUILDER --- */
async function loadMovieManager() {
  const select = $("#movieLessonSelect");
  if (select && state.lessons && state.lessons.length > 0) {
    select.innerHTML = state.lessons.map(l => `<option value="${escH(l.lesson_id)}">${escH(l.title || l.lesson_id)}</option>`).join("");
    if (!_currentMovieLessonId && state.lessons[0]) {
      _currentMovieLessonId = state.lessons[0].lesson_id;
    }
    if (_currentMovieLessonId) select.value = _currentMovieLessonId;
  }

  if (_currentMovieLessonId) {
    await loadLessonMovieConfig(_currentMovieLessonId);
  }
  await loadMovieJobs();
}

async function loadLessonMovieConfig(lessonId) {
  _currentMovieLessonId = lessonId;
  try {
    const data = await api(`/api/studio/cms/movie-config?lesson_id=${encodeURIComponent(lessonId)}`);
    _currentMovieConfig = data.config || { scenes: [] };
    renderMovieScenes();
  } catch(e) {
    console.warn("Failed to load movie config:", e);
    _currentMovieConfig = { scenes: [] };
    renderMovieScenes();
  }
}

function renderMovieScenes() {
  const container = $("#movieScenesContainer");
  if (!container) return;
  const scenes = _currentMovieConfig.scenes || [];
  if (scenes.length === 0) {
    container.innerHTML = `<div style="text-align:center;color:var(--muted);padding:20px">Для этого урока ещё не добавлены сцены мультфильма. Нажмите «+ Добавить сцену».</div>`;
    return;
  }

  container.innerHTML = scenes.map((s, idx) => `
    <div class="card" style="background:#F8FAFC;border:1px solid #E2E8F0;padding:12px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <strong>Сцена #${idx + 1}: ${escH(s.title || 'Безымянная сцена')}</strong>
        <button class="danger small-btn" onclick="deleteMovieScene(${idx})">Удалить</button>
      </div>
      <div class="two">
        <label style="font-size:12px;font-weight:600">Название сцены<input value="${escH(s.title || '')}" onchange="_currentMovieConfig.scenes[${idx}].title=this.value" style="width:100%;margin-top:2px"></label>
        <label style="font-size:12px;font-weight:600">Длительность (сек)<input type="number" step="0.5" value="${s.duration || 4.0}" onchange="_currentMovieConfig.scenes[${idx}].duration=parseFloat(this.value)" style="width:100%;margin-top:2px"></label>
      </div>
      <div class="two" style="margin-top:6px">
        <label style="font-size:12px;font-weight:600">Движение героя<input value="${escH(s.animation || 'cheer')}" onchange="_currentMovieConfig.scenes[${idx}].animation=this.value" placeholder="wave, jump, cheer" style="width:100%;margin-top:2px"></label>
        <label style="font-size:12px;font-weight:600">Фон сцены<input value="${escH(s.background || 'room_default')}" onchange="_currentMovieConfig.scenes[${idx}].background=this.value" placeholder="forest, room, space" style="width:100%;margin-top:2px"></label>
      </div>
      <label style="font-size:12px;font-weight:600;margin-top:6px;display:block">Реплика персонажа<input value="${escH(s.dialogue || '')}" onchange="_currentMovieConfig.scenes[${idx}].dialogue=this.value" placeholder="Отлично справились!" style="width:100%;margin-top:2px"></label>
    </div>
  `).join("");
}

function addNewMovieScene() {
  if (!_currentMovieConfig.scenes) _currentMovieConfig.scenes = [];
  _currentMovieConfig.scenes.push({
    title: `Сцена ${_currentMovieConfig.scenes.length + 1}`,
    duration: 3.5,
    animation: "cheer",
    background: "room_default",
    dialogue: "Молодец! Ты отлично говоришь!"
  });
  renderMovieScenes();
}

function deleteMovieScene(idx) {
  _currentMovieConfig.scenes.splice(idx, 1);
  renderMovieScenes();
}

async function saveMovieConfig() {
  if (!_currentMovieLessonId) {
    alert("Выберите урок");
    return;
  }
  try {
    await api("/api/studio/cms/movie-config", {
      method: "POST",
      body: JSON.stringify({
        lesson_id: _currentMovieLessonId,
        config: _currentMovieConfig
      })
    });
    notice("✅ Таймлайн мультфильма успешно сохранён!");
  } catch(e) {
    alert("Ошибка сохранения мультфильма: " + e.message);
  }
}

async function publishMovieConfig() {
  if (!_currentMovieLessonId) return;
  try {
    await api(`/api/studio/cms/movie-config/publish?lesson_id=${encodeURIComponent(_currentMovieLessonId)}`, {
      method: "POST"
    });
    notice("🚀 Мультфильм опубликован! Мобильные устройства получат обновлённый сценарий без пересборки APK.");
  } catch(e) {
    alert("Ошибка публикации: " + e.message);
  }
}

async function loadMovieJobs() {
  const container = $("#movieJobsList");
  if (!container) return;
  try {
    const data = await api("/api/studio/cms/movie-jobs");
    const jobs = data.jobs || [];
    if (jobs.length === 0) {
      container.innerHTML = '<div style="color:var(--muted);text-align:center;padding:12px">Очередь генерации пуста</div>';
      return;
    }
    container.innerHTML = jobs.map(j => `
      <div style="background:#F8FAFC;border:1px solid #E2E8F0;border-radius:6px;padding:8px;font-size:12px;display:flex;flex-direction:column;gap:3px">
        <div style="display:flex;justify-content:space-between">
          <strong>Урок: ${escH(j.lesson_id)}</strong>
          <span style="font-weight:700;color:${j.status === 'COMPLETED' ? '#15803D' : (j.status === 'FAILED' ? '#DC2626' : '#D97706')}">${escH(j.status)}</span>
        </div>
        <div style="color:var(--muted)">Ребёнок ID: ${j.child_id} | ${new Date(j.created_at || Date.now()).toLocaleTimeString()}</div>
        ${j.error ? `<div style="color:var(--danger);font-size:11px">${escH(j.error)}</div>` : ''}
        ${j.status === 'FAILED' ? `<button class="primary small-btn" style="padding:2px 6px;margin-top:2px" onclick="retryMovieJob('${j.id}')">🔄 Повторить</button>` : ''}
      </div>
    `).join("");
  } catch(e) {
    console.warn("Failed to load movie jobs:", e);
  }
}

async function retryMovieJob(jobId) {
  try {
    await api(`/api/studio/cms/movie-jobs/${jobId}/retry`, { method: "POST" });
    notice("✅ Перезапуск генерации мультфильма выполнен");
    await loadMovieJobs();
  } catch(e) {
    alert("Ошибка перезапуска: " + e.message);
  }
}


/* --- 5. AI & LANGUAGE SETTINGS --- */
async function loadAiSettings() {
  try {
    const data = await api("/api/studio/cms/ai-settings");
    const s = data.settings || {};
    if ($("#aiPersonaName")) $("#aiPersonaName").value = s.persona_name || "Кот DOME";
    if ($("#aiPersonaRole")) $("#aiPersonaRole").value = s.persona_role || "Добрый и ободряющий тьютор-наставник";
    if ($("#aiSystemPrompt")) $("#aiSystemPrompt").value = s.system_prompt || "";
    if ($("#aiTtsProvider")) $("#aiTtsProvider").value = s.tts_provider || "cartesia";
    if ($("#aiLlmModel")) $("#aiLlmModel").value = s.llm_model || "gemini-2.0-flash";
    if ($("#aiTemperature")) $("#aiTemperature").value = s.temperature ?? 0.3;
    if ($("#aiMaxWords")) $("#aiMaxWords").value = s.max_words ?? 20;
    if ($("#aiStrictImmersion")) $("#aiStrictImmersion").checked = !!s.strict_immersion;
    if ($("#aiChildSafeFilter")) $("#aiChildSafeFilter").checked = s.child_safe_filter !== false;
  } catch(e) {
    console.warn("Failed to load AI settings:", e);
  }
}

async function saveAiSettings() {
  try {
    const payload = {
      persona_name: $("#aiPersonaName")?.value?.trim(),
      persona_role: $("#aiPersonaRole")?.value?.trim(),
      system_prompt: $("#aiSystemPrompt")?.value?.trim(),
      tts_provider: $("#aiTtsProvider")?.value,
      llm_model: $("#aiLlmModel")?.value,
      temperature: parseFloat($("#aiTemperature")?.value || "0.3"),
      max_words: parseInt($("#aiMaxWords")?.value || "20", 10),
      strict_immersion: $("#aiStrictImmersion")?.checked,
      child_safe_filter: $("#aiChildSafeFilter")?.checked
    };
    await api("/api/studio/cms/ai-settings", {
      method: "POST",
      body: JSON.stringify(payload)
    });
    notice("✅ Настройки AI-Тьютора успешно сохранены!");
  } catch(e) {
    alert("Ошибка сохранения: " + e.message);
  }
}


/* --- 6. LOGS & DIAGNOSTICS --- */
async function loadDiagnosticsLogs() {
  const level = $("#logLevelFilter")?.value || "ALL";
  const search = $("#logSearch")?.value?.trim() || "";
  const out = $("#logsOutput");
  if (out) out.textContent = "Загрузка журнала...";

  try {
    const params = new URLSearchParams({ level, search });
    const data = await api(`/api/studio/cms/logs?${params}`);
    if (out) out.textContent = (data.logs || []).join("\\n") || "Нет записей лога за последнее время";

    // Also load latency summary
    const latData = await api("/api/studio/cms/latency");
    const latSummary = $("#latencyMetricsSummary");
    if (latSummary && latData.metrics) {
      const m = latData.metrics;
      latSummary.innerHTML = `
        <div class="stat-card"><div class="stat-value">${m.avg_stt_ms || 120}мс</div><div class="stat-label">Распознавание речи (STT)</div></div>
        <div class="stat-card"><div class="stat-value">${m.avg_llm_ms || 340}мс</div><div class="stat-label">Ответ AI Тьютора (LLM)</div></div>
        <div class="stat-card"><div class="stat-value">${m.avg_tts_ms || 110}мс</div><div class="stat-label">Синтез голоса (TTS)</div></div>
        <div class="stat-card"><div class="stat-value" style="color:${m.error_rate_pct > 2 ? '#DC2626' : '#15803D'}">${m.error_rate_pct || 0.1}%</div><div class="stat-label">Ошибки озвучки / таймауты</div></div>
      `;
    }
  } catch(e) {
    if (out) out.textContent = "Ошибка получения логов: " + e.message;
  }
}


/* --- 7. VERSIONS & ROLLBACK --- */
async function loadGlobalVersions() {
  const list = $("#globalVersionsList");
  if (!list) return;
  list.innerHTML = '<div class="card" style="text-align:center;color:var(--muted)">Загрузка резервных версий...</div>';

  try {
    const data = await api("/api/studio/cms/versions");
    const versions = data.versions || [];
    if (versions.length === 0) {
      list.innerHTML = '<div class="card" style="text-align:center;color:var(--muted);padding:24px">Пока нет архивных версий контента</div>';
      return;
    }

    list.innerHTML = versions.map(v => `
      <div class="card" style="display:flex;justify-content:space-between;align-items:center;padding:12px 16px">
        <div>
          <strong>${escH(v.lesson_title || v.lesson_id)}</strong>
          <div style="font-size:12px;color:var(--muted)">Версия: <code>${escH(v.version_id)}</code> • Дата: ${new Date(v.timestamp || Date.now()).toLocaleString()} • Автор: ${escH(v.author || 'admin')}</div>
          <div style="font-size:12px;margin-top:2px">${escH(v.summary || 'Автоматический бэкап')}</div>
        </div>
        <button class="primary" style="padding:6px 14px" onclick="rollbackGlobalVersion('${v.lesson_id}', '${v.version_id}')">↩ Восстановить в черновик</button>
      </div>
    `).join("");
  } catch(e) {
    list.innerHTML = `<div class="card" style="color:var(--danger)">Ошибка загрузки версий: ${escH(e.message)}</div>`;
  }
}

async function rollbackGlobalVersion(lessonId, versionId) {
  if (!confirm(`Восстановить версию ${versionId} для урока ${lessonId}? Текущий опубликованный урок не изменится до нажатия «Опубликовать».`)) return;
  try {
    await api("/api/studio/cms/versions/rollback", {
      method: "POST",
      body: JSON.stringify({ lesson_id: lessonId, version_id: versionId })
    });
    notice(`✅ Версия ${versionId} успешно восстановлена в черновик!`);
  } catch(e) {
    alert("Ошибка восстановления: " + e.message);
  }
}


/* --- 8. PLATFORM SETTINGS & FEATURE FLAGS --- */
async function loadPlatformSettings() {
  try {
    const data = await api("/api/studio/cms/settings");
    const regMode = data.registration_mode || "AUTO";
    const radio = document.querySelector(`input[name="platformRegMode"][value="${regMode}"]`);
    if (radio) radio.checked = true;

    const list = $("#featureFlagsList");
    if (list && data.features) {
      const labels = {
        custom_heroes: "🎨 Рисунки детей в героев (Child Drawing to Avatar)",
        new_animation_engine: "⚡ Высокочастотная плавная анимация (60fps Engine)",
        voice_homework: "🎙️ Голосовые домашние задания с оценкой AI",
        offline_mode: "💾 Кеширование уроков для работы без интернета",
        strict_language_immersion: "🌍 Строгое языковое погружение (без перевода)",
        free_trial_auto_grant: "🎁 Автоматическое начисление пробного периода"
      };

      list.innerHTML = Object.entries(data.features).map(([key, enabled]) => `
        <label style="display:flex;align-items:center;justify-content:space-between;padding:8px 12px;background:#F8FAFC;border:1px solid #E2E8F0;border-radius:6px;cursor:pointer">
          <div>
            <strong>${labels[key] || key}</strong>
            <div style="font-size:11px;color:var(--muted)">Ключ: <code>${key}</code></div>
          </div>
          <input type="checkbox" id="feature_${key}" ${enabled ? "checked" : ""} style="transform:scale(1.2)">
        </label>
      `).join("");
    }
  } catch(e) {
    console.warn("Failed to load platform settings:", e);
  }
}

async function savePlatformSettings() {
  try {
    const regRadio = document.querySelector('input[name="platformRegMode"]:checked');
    const registration_mode = regRadio ? regRadio.value : "AUTO";

    const features = {};
    $$('#featureFlagsList input[type="checkbox"]').forEach(cb => {
      const key = cb.id.replace('feature_', '');
      features[key] = cb.checked;
    });

    await api("/api/studio/cms/settings", {
      method: "POST",
      body: JSON.stringify({ registration_mode, features })
    });
    notice("✅ Настройки платформы и флаги функционала успешно сохранены!");
  } catch(e) {
    alert("Ошибка сохранения: " + e.message);
  }
}

