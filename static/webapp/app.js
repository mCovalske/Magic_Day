const tg = window.Telegram?.WebApp;
const state = { user:null, route:"home", data:{} };

function init(){
  if(tg){
    tg.ready();
    tg.expand();
    tg.setHeaderColor("#0b0820");
    tg.setBackgroundColor("#080617");
  }

  document.querySelectorAll(".bottom-nav button").forEach(
    b => b.addEventListener("click", () => go(b.dataset.route))
  );

  document.getElementById("backBtn").addEventListener("click", () => go("home"));
  document.getElementById("closeBtn").addEventListener("click", () => tg?.close());

  boot();
}

function initData(){
  return tg?.initData || "";
}

async function api(path, options={}){
  const headers = {
    "Content-Type":"application/json",
    "X-Telegram-Init-Data":initData()
  };

  const r = await fetch(path, {
    ...options,
    headers:{
      ...headers,
      ...(options.headers || {})
    }
  });

  let d = null;

  try{
    d = await r.json();
  }catch{}

  if(!r.ok){
    throw new Error(d?.error || `Ошибка запроса (${r.status})`);
  }

  return d;
}

async function boot(){
  try{
    const d = await api("/api/webapp/auth", {
      method:"POST",
      body:"{}"
    });

    state.user = d.user;
    state.data = d;

    renderHome();

  }catch(e){
    document.getElementById("screen").innerHTML = `
      <div class="notice warn">
        Не удалось открыть приложение.<br>
        ${esc(e.message)}
      </div>
    `;
  }
}

function go(route){
  state.route = route;
  render();
}

function render(){
  document
    .querySelectorAll(".bottom-nav button")
    .forEach(b => b.classList.toggle("active", b.dataset.route === state.route));

  document
    .getElementById("backBtn")
    .classList.toggle("hidden", state.route === "home");

  if(state.route === "home") renderHome();
  else if(state.route === "forecast") renderForecast();
  else if(state.route === "magic8") renderMagic8();
  else if(state.route === "catalog") renderCatalog();
  else if(state.route === "profile") renderProfile();
  else if(state.route === "admin") renderAdmin();
  else if(state.route === "docs") renderDocs();
  else if(state.route === "notifications") renderNotifications();
  else renderHome();
}

function hero(img,title,sub,desc=""){
  return `
    <section class="hero">
      <img src="/static/webapp/images/${img}" alt="${esc(title)}">

      <div class="hero-content">
        <div class="eyebrow">${esc(sub)}</div>
        <h1>${esc(title)}</h1>
        ${desc ? `<p>${esc(desc)}</p>` : ""}
      </div>
    </section>
  `;
}

function card(img,title,desc,click){
  return `
    <button
      class="card action-card"
      style="background-image:url('/static/webapp/images/${img}')"
      onclick="${click}"
    >
      <div class="shade">
        <h3>${esc(title)}</h3>
        <p>${esc(desc)}</p>
      </div>
    </button>
  `;
}

function renderHome(){

  const u = state.user || {};
  const admin = !!state.data?.is_admin;

  const adminButton = admin
    ? `<button class="btn gold" onclick="go('admin')">👑 Админ-панель</button>`
    : "";

  document.getElementById("brandSub").textContent =
    u.name
      ? `Добро пожаловать, ${u.name}`
      : "Пространство интуиции";

  const consent = u.consent_given;

  document.getElementById("screen").innerHTML =
    hero(
      "welcome_hero.png",
      "Добро пожаловать",
      "Пространство предсказаний",
      "Интуиция, символы, персональные прогнозы и древние традиции в одном месте."
    )

    +

    (
      !consent
        ?
        `
          <div class="notice warn">
            <b>Перед началом</b><br>
            Ознакомьтесь с документами и подтвердите согласие
            на обработку персональных данных.

            <button
              class="btn gold"
              onclick="showConsent()"
            >
              Ознакомиться и согласиться
            </button>
          </div>
        `
        :
        ""
    )

    +

    `
      <div class="grid">

        ${card(
          "daily_forecast.png",
          "Предсказание на день",
          "Узнайте, что приготовил сегодняшний день.",
          "renderForecast()"
        )}

        ${card(
          "personal_forecast.png",
          "Персональный прогноз",
          "Прогноз на основе ваших данных.",
          "startPersonal()"
        )}

        ${card(
          "spheres.png",
          "Прогноз по сферам",
          "Отношения, работа, финансы и развитие.",
          "renderSpheres()"
        )}

        ${card(
          "magic8.png",
          "Чёрный шар 8",
          "Задайте вопрос и получите ответ.",
          "go('magic8')"
        )}

        ${card(
          "catalog.png",
          "Каталог бусин Дзи",
          "Выберите символ и украшение.",
          "go('catalog')"
        )}

        ${card(
          "gift.png",
          "Подарок другу",
          "Отправьте предсказание близкому человеку.",
          "showGift()"
        )}

        ${card(
          "notifications.png",
          "Уведомления",
          "Настройте ежедневные напоминания.",
          "go('notifications')"
        )}

        ${card(
          "profile.png",
          "Личный кабинет",
          "Профиль, заказы, данные и настройки.",
          "go('profile')"
        )}

        ${card(
          "my_day.png",
          "Мой день",
          "Персональный обзор сегодняшнего дня.",
          "showMyDay()"
        )}

        ${card(
          "legal.png",
          "Правовые документы",
          "Политика, согласия и условия.",
          "go('docs')"
        )}

      </div>

      ${adminButton}
    `;
}

async function ensureConsent(){
  if(
    state.user?.consent_given &&
    state.user?.consent_version
  ){
    return true;
  }

  showConsent();

  return false;
}

function showConsent(){

  openModal(`
    <h2>Согласие</h2>

    <p class="small">
      Перед использованием приложения ознакомьтесь с документами.
    </p>

    <button
      class="btn"
      onclick="openDoc('01_policy_personal_data.html')"
    >
      Политика обработки ПДн
    </button>

    <button
      class="btn"
      onclick="openDoc('02_consent_personal_data.html')"
    >
      Согласие на обработку ПДн
    </button>

    <button
      class="btn"
      onclick="openDoc('03_confidentiality_security.html')"
    >
      Конфиденциальность
    </button>

    <button
      class="btn gold"
      onclick="acceptConsent()"
    >
      ✅ Ознакомился(лась) и даю согласие
    </button>

    <button
      class="btn danger"
      onclick="closeModal()"
    >
      ❌ Не согласен(на)
    </button>
  `);
}

async function acceptConsent(){

  try{

    const d = await api("/api/webapp/consent",{
      method:"POST",
      body:"{}"
    });

    state.user = d.user;

    closeModal();

    toast("Согласие сохранено");

    renderHome();

  }catch(e){

    toast(e.message,true);

  }
}

async function renderForecast(){

  if(!(await ensureConsent())){
    return;
  }

  document.getElementById("screen").innerHTML =
    hero(
      "daily_forecast.png",
      "Предсказание на день",
      "Сегодня",
      "Подождите немного — формируем ваш прогноз."
    );

  const steps = [
    "🔮 Анализирую энергию дня…",
    "✨ Сопоставляю ключевые тенденции…",
    "🌙 Формирую прогноз…"
  ];

  const box = document.createElement("div");

  box.className = "forecast";

  box.innerHTML = `<div id="steps"></div>`;

  document.getElementById("screen").appendChild(box);

  for(const s of steps){

    document.getElementById("steps").innerHTML += `
      <div>${s}</div>
    `;

    await sleep(500);
  }

  try{

    const d = await api(
      "/api/webapp/forecast/daily",
      {
        method:"POST",
        body:"{}"
      }
    );

    document
      .getElementById("screen")
      .insertAdjacentHTML(
        "beforeend",
        `<div class="forecast">${d.html}</div>`
      );

  }catch(e){

    toast(e.message,true);

  }
}

async function startPersonal(sphere=null){

  if(!(await ensureConsent())){
    return;
  }

  openModal(`
    <h2>
      ${sphere
        ? "Персональный прогноз по сфере"
        : "Персональный прогноз"}
    </h2>

    <label class="label">Имя</label>

    <input
      id="pName"
      class="input"
      value="${esc(state.user?.name || "")}"
    >

    <label class="label">Пол</label>

    <select id="pGender" class="input">

      <option value="male">
        Мужчина
      </option>

      <option value="female">
        Женщина
      </option>

      <option value="other">
        Не хочу указывать
      </option>

    </select>

    <label class="label">Дата рождения</label>

    <input
      id="pBirth"
      class="input"
      placeholder="ДД.ММ.ГГГГ"
      value="${esc(state.user?.birthdate || "")}"
    >

    ${
      sphere
        ?
        `<div class="notice">
          Сфера: <b>${esc(sphere)}</b>
        </div>`
        :
        ""
    }

    <button
      class="btn gold"
      onclick="submitPersonal(${sphere ? JSON.stringify(sphere) : "null"})"
    >
      Получить прогноз
    </button>
  `);
}

async function submitPersonal(sphere){

  try{

    const body = {
      name:val("pName"),
      gender:val("pGender"),
      birthdate:val("pBirth"),
      sphere
    };

    const d = await api(
      "/api/webapp/forecast/personal",
      {
        method:"POST",
        body:JSON.stringify(body)
      }
    );

    closeModal();

    document.getElementById("screen").innerHTML =
      hero(
        "personal_forecast.png",
        "Персональный прогноз",
        "Для вас",
        "Ваш прогноз сформирован."
      );

    document
      .getElementById("screen")
      .insertAdjacentHTML(
        "beforeend",
        `<div class="forecast">${d.html}</div>`
      );

    state.user = d.user;

  }catch(e){

    toast(e.message,true);

  }
}

function renderSpheres(){

  const items = [
    ["❤️ Любовь","love"],
    ["💼 Карьера","career"],
    ["💰 Финансы","finance"],
    ["👨‍👩‍👧 Семья","family"],
    ["🌿 Самочувствие","health"],
    ["📚 Развитие","growth"]
  ];

  document.getElementById("screen").innerHTML =
    hero(
      "spheres.png",
      "Прогноз по сферам",
      "Жизненные направления",
      "Выберите сферу."
    );

  document
    .getElementById("screen")
    .insertAdjacentHTML(
      "beforeend",
      `
        <div class="grid">

          ${
            items.map(
              x =>
                `
                  <button
                    class="btn"
                    onclick="startPersonal(${JSON.stringify(x[1])})"
                  >
                    ${x[0]}
                  </button>
                `
            ).join("")
          }

        </div>
      `
    );
}

async function renderMagic8(){

  if(!(await ensureConsent())){
    return;
  }

  document.getElementById("screen").innerHTML =
    hero(
      "magic8.png",
      "Чёрный шар 8",
      "Да / Нет",
      "Три вопроса в день."
    );

  document
    .getElementById("screen")
    .insertAdjacentHTML(
      "beforeend",
      `
        <div class="notice">
          Сформулируйте вопрос так,
          чтобы на него можно было ответить
          «Да» или «Нет».
        </div>

        <button
          class="btn gold"
          onclick="askMagic()"
        >
          Задать вопрос
        </button>
      `
    );
}

async function askMagic(){

  openModal(`
    <h2>Ваш вопрос</h2>

    <input
      id="magicQ"
      class="input"
      maxlength="250"
      placeholder="Например: стоит ли мне начинать новый проект?"
    >

    <button
      class="btn gold"
      onclick="submitMagic()"
    >
      Получить ответ
    </button>
  `);
}

async function submitMagic(){

  try{

    const d = await api(
      "/api/webapp/magic8/ask",
      {
        method:"POST",
        body:JSON.stringify({
          question:val("magicQ")
        })
      }
    );

    closeModal();

    document
      .getElementById("screen")
      .insertAdjacentHTML(
        "beforeend",
        `
          <div class="forecast center">

            <h2>
              🎱 ${esc(d.answer)}
            </h2>

            <p>
              Осталось вопросов сегодня:
              ${d.remaining}
            </p>

          </div>
        `
      );

  }catch(e){

    toast(e.message,true);

  }
}

async function renderCatalog(){

  if(!(await ensureConsent())){
    return;
  }

  try{

    const d = await api("/api/webapp/catalog");

    document.getElementById("screen").innerHTML =
      hero(
        "catalog.png",
        "Каталог бусин Дзи",
        "Коллекция",
        "Выберите бусину."
      );

    const list = d.products
      .map(
        p =>
          `
            <div class="list-item">

              <img
                class="thumb"
                src="${
                  p.image_file
                    ? "/static/images/" + esc(p.image_file)
                    : "/static/webapp/images/catalog.png"
                }"
              >

              <div class="grow">

                <h3>
                  ${esc(p.name)}
                </h3>

                <div class="meta">
                  ${esc(p.description)}
                </div>

                <div class="price">
                  ${Number(p.price_rub).toLocaleString("ru-RU")} ₽
                </div>

                <button
                  class="btn gold"
                  onclick="showProduct(${p.id})"
                >
                  Подробнее
                </button>

              </div>

            </div>
          `
      )
      .join("");

    document
      .getElementById("screen")
      .insertAdjacentHTML(
        "beforeend",
        `
          <div class="list">
            ${
              list ||
              '<div class="empty">Каталог пока пуст.</div>'
            }
          </div>
        `
      );

  }catch(e){

    toast(e.message,true);

  }
}

async function showProduct(id){

  try{

    const d = await api(
      `/api/webapp/catalog/${id}`
    );

    openModal(`

      <h2>
        ${esc(d.product.name)}
      </h2>

      <img
        src="${
          d.product.image_file
            ? "/static/images/" + esc(d.product.image_file)
            : "/static/webapp/images/catalog.png"
        }"
        style="
          width:100%;
          border-radius:18px;
          border:1px solid var(--border)
        "
      >

      <p>
        ${esc(d.product.description)}
      </p>

      <div class="price">
        ${Number(d.product.price_rub).toLocaleString("ru-RU")} ₽
      </div>

      <button
        class="btn gold"
        onclick="startOrder(${d.product.id})"
      >
        🛒 Заказать
      </button>

    `);

  }catch(e){

    toast(e.message,true);

  }
}

async function startOrder(id){

  closeModal();

  openModal(`
    <h2>Оформление заказа</h2>

    <input
      id="oName"
      class="input"
      placeholder="Имя"
      value="${esc(state.user?.name || "")}"
    >

    <input
      id="oPhone"
      class="input"
      placeholder="+7..."
      value="${esc(state.user?.phone || "")}"
    >

    <button
      class="btn gold"
      onclick="submitOrder(${id})"
    >
      Подтвердить
    </button>
  `);
}

async function submitOrder(id){

  try{

    const d = await api(
      "/api/webapp/orders",
      {
        method:"POST",
        body:JSON.stringify({
          product_id:id,
          name:val("oName"),
          phone:val("oPhone")
        })
      }
    );

    closeModal();

    toast(`Заказ №${d.order_id} принят`);

  }catch(e){

    toast(e.message,true);

  }
}

async function renderNotifications(){

  if(!(await ensureConsent())){
    return;
  }

  try{

    const d = await api(
      "/api/webapp/subscription"
    );

    document.getElementById("screen").innerHTML =
      hero(
        "notifications.png",
        "Уведомления",
        "Каждый день",
        "Получайте приглашение открыть приложение и прочитать прогноз."
      );

    document
      .getElementById("screen")
      .insertAdjacentHTML(
        "beforeend",
        `
          <div class="card">

            <div class="badge">
              ${d.active ? "Включены" : "Выключены"}
            </div>

            <label class="label">
              Время
            </label>

            <input
              id="subTime"
              class="input"
              value="${esc(d.time || "09:00")}"
              type="time"
            >

            <button
              class="btn gold"
              onclick="saveSubscription()"
            >
              ${
                d.active
                  ? "Сохранить время"
                  : "Включить уведомления"
              }
            </button>

            ${
              d.active
                ?
                `<button
                  class="btn danger"
                  onclick="disableSubscription()"
                >
                  🔕 Отписаться
                </button>`
                :
                ""
            }

          </div>
        `
      );

  }catch(e){

    toast(e.message,true);

  }
}

async function saveSubscription(){

  try{

    await api(
      "/api/webapp/subscription",
      {
        method:"POST",
        body:JSON.stringify({
          time:val("subTime"),
          active:true
        })
      }
    );

    toast("Уведомления включены");

    renderNotifications();

  }catch(e){

    toast(e.message,true);

  }
}

async function disableSubscription(){

  try{

    await api(
      "/api/webapp/subscription",
      {
        method:"POST",
        body:JSON.stringify({
          time:"09:00",
          active:false
        })
      }
    );

    toast("Уведомления выключены");

    renderNotifications();

  }catch(e){

    toast(e.message,true);

  }
}

async function renderProfile(){

  if(!(await ensureConsent())){
    return;
  }

  try{

    const u = state.user;

    const orders = await api(
      "/api/webapp/orders"
    );

    document.getElementById("screen").innerHTML =
      hero(
        "profile.png",
        "Личный кабинет",
        "Ваш профиль"
      );

    document
      .getElementById("screen")
      .insertAdjacentHTML(
        "beforeend",
        `
          <div class="card">

            <div class="kpi">

              <div>
                <div class="small">Имя</div>
                <strong>${esc(u.name || "—")}</strong>
              </div>

              <div>
                <div class="small">Telegram</div>
                <strong>
                  @${esc(u.username || "—")}
                </strong>
              </div>

            </div>

            <div class="divider"></div>

            <div class="meta">
              Пол: ${esc(u.gender || "—")}
            </div>

            <div class="meta">
              Дата рождения: ${esc(u.birthdate || "—")}
            </div>

            <div class="meta">
              Телефон: ${esc(u.phone || "—")}
            </div>

            <button
              class="btn"
              onclick="editProfile()"
            >
              ✏️ Изменить данные
            </button>

          </div>

          <h2 class="section-title">
            Мои заказы
          </h2>

          <div class="list">

            ${
              orders.orders.length

                ?

                orders.orders
                  .map(
                    o =>
                      `
                        <div class="list-item">

                          <div class="grow">

                            <h3>
                              Заказ №${o.id}
                            </h3>

                            <div class="meta">
                              ${esc(o.product_name)}
                              ·
                              ${esc(o.status_label)}
                            </div>

                            ${
                              o.status === "completed"
                                ?
                                `
                                  <button
                                    class="btn"
                                    onclick="openReview(${o.id})"
                                  >
                                    ⭐ Оставить отзыв
                                  </button>
                                `
                                :
                                ""
                            }

                          </div>

                        </div>
                      `
                  )
                  .join("")

                :

                "<div class='empty'>Заказов пока нет.</div>"
            }

          </div>

          <button
            class="btn"
            onclick="showMyDay()"
          >
            📊 Мой день
          </button>

          <button
            class="btn"
            onclick="go('notifications')"
          >
            🔔 Уведомления
          </button>

          <button
            class="btn"
            onclick="go('docs')"
          >
            📄 Правовые документы
          </button>

          <button
            class="btn danger"
            onclick="deleteMyData()"
          >
            🗑 Удалить мои данные
          </button>
        `
      );

  }catch(e){

    toast(e.message,true);

  }
}

function openReview(orderId){

  openModal(`
    <h2>⭐ Отзыв</h2>

    <label class="label">
      Оценка
    </label>

    <select
      id="reviewRating"
      class="input"
    >
      <option value="5">
        ⭐⭐⭐⭐⭐ 5
      </option>

      <option value="4">
        ⭐⭐⭐⭐ 4
      </option>

      <option value="3">
        ⭐⭐⭐ 3
      </option>

      <option value="2">
        ⭐⭐ 2
      </option>

      <option value="1">
        ⭐ 1
      </option>
    </select>

    <label class="label">
      Комментарий
    </label>

    <textarea
      id="reviewText"
      class="input"
      rows="5"
      placeholder="Поделитесь впечатлениями"
    ></textarea>

    <button
      class="btn gold"
      onclick="submitReview(${orderId})"
    >
      Опубликовать отзыв
    </button>
  `);
}

async function submitReview(orderId){

  try{

    await api(
      "/api/webapp/reviews",
      {
        method:"POST",
        body:JSON.stringify({
          order_id:orderId,
          rating:Number(val("reviewRating")),
          text:val("reviewText")
        })
      }
    );

    closeModal();

    toast("Спасибо за отзыв");

    renderProfile();

  }catch(e){

    toast(e.message,true);

  }
}

function editProfile(){

  openModal(`
    <h2>Данные</h2>

    <input
      id="eName"
      class="input"
      placeholder="Имя"
      value="${esc(state.user.name || "")}"
    >

    <select
      id="eGender"
      class="input"
    >
      <option value="male">
        Мужчина
      </option>

      <option value="female">
        Женщина
      </option>

      <option value="other">
        Не хочу указывать
      </option>
    </select>

    <input
      id="eBirth"
      class="input"
      placeholder="ДД.ММ.ГГГГ"
      value="${esc(state.user.birthdate || "")}"
    >

    <input
      id="ePhone"
      class="input"
      placeholder="+7..."
      value="${esc(state.user.phone || "")}"
    >

    <button
      class="btn gold"
      onclick="saveProfile()"
    >
      Сохранить
    </button>
  `);
}

async function saveProfile(){

  try{

    const d = await api(
      "/api/webapp/profile",
      {
        method:"POST",
        body:JSON.stringify({
          name:val("eName"),
          gender:val("eGender"),
          birthdate:val("eBirth"),
          phone:val("ePhone")
        })
      }
    );

    state.user = d.user;

    closeModal();

    renderProfile();

    toast("Данные обновлены");

  }catch(e){

    toast(e.message,true);

  }
}

async function deleteMyData(){

  if(!confirm("Удалить профиль и связанные данные?")){
    return;
  }

  try{

    await api(
      "/api/webapp/profile/delete",
      {
        method:"POST",
        body:"{}"
      }
    );

    tg?.close();

  }catch(e){

    toast(e.message,true);

  }
}

async function showMyDay(){

  if(!(await ensureConsent())){
    return;
  }

  try{

    const d = await api(
      "/api/webapp/my-day"
    );

    document.getElementById("screen").innerHTML =
      hero(
        "my_day.png",
        "Мой день",
        "Сегодня",
        "Ваш персональный обзор."
      );

    document
      .getElementById("screen")
      .insertAdjacentHTML(
        "beforeend",
        `<div class="forecast">${d.html}</div>`
      );

  }catch(e){

    toast(e.message,true);

  }
}

async function showGift(){

  if(!(await ensureConsent())){
    return;
  }

  openModal(`
    <h2>Подарок другу</h2>

    <p class="small">
      Получатель должен хотя бы один раз открыть бота.
    </p>

    <input
      id="giftUser"
      class="input"
      placeholder="@username"
    >

    <button
      class="btn gold"
      onclick="sendGift()"
    >
      🎁 Отправить предсказание
    </button>
  `);
}

async function sendGift(){

  try{

    await api(
      "/api/webapp/gift/prediction",
      {
        method:"POST",
        body:JSON.stringify({
          username:val("giftUser")
        })
      }
    );

    closeModal();

    toast("Предсказание отправлено");

  }catch(e){

    toast(e.message,true);

  }
}

async function renderDocs(){

  document.getElementById("screen").innerHTML =
    hero(
      "legal.png",
      "Правовые документы",
      "Документы",
      "Выберите документ."
    );

  try{

    const d = await api(
      "/api/webapp/legal"
    );

    document
      .getElementById("screen")
      .insertAdjacentHTML(
        "beforeend",
        `
          <div class="list">

            ${
              d.documents
                .map(
                  x =>
                    `
                      <button
                        class="btn"
                        onclick="openDoc('${esc(x.file)}')"
                      >
                        ${esc(x.title)}
                      </button>
                    `
                )
                .join("")
            }

          </div>
        `
      );

  }catch(e){

    toast(e.message,true);

  }
}

function openDoc(file){

  if(!file){
    toast("Документ недоступен",true);
    return;
  }

  const url =
    `/legal/${encodeURIComponent(file)}`;

  if(tg?.openLink){
    tg.openLink(url);
  }else{
    window.open(url,"_blank","noopener");
  }
}

async function renderAdmin(){

  if(!state.data?.is_admin){
    toast("Недоступно");
    return;
  }

  try{

    const d = await api(
      "/api/webapp/admin/analytics?days="
    );

    document.getElementById("screen").innerHTML =
      `
        <h2 class="section-title">
          👑 Админ-панель
        </h2>

        <div class="kpi">

          ${
            Object
              .entries(d.stats)
              .slice(0,8)
              .map(
                ([k,v]) =>
                  `
                    <div class="card">

                      <div class="small">
                        ${esc(k)}
                      </div>

                      <strong>
                        ${esc(v)}
                      </strong>

                    </div>
                  `
              )
              .join("")
          }

        </div>

        <div class="grid">

          <button class="btn" onclick="adminOrders()">
            📦 Заказы
          </button>

          <button class="btn" onclick="adminUsers()">
            👥 Пользователи
          </button>

          <button class="btn" onclick="adminProducts()">
            🛍 Каталог
          </button>

          <button class="btn" onclick="adminContent()">
            📝 Контент
          </button>

          <button class="btn" onclick="adminMagic()">
            🎱 Magic 8
          </button>

          <button class="btn" onclick="adminAudit()">
            🛡 Журнал
          </button>

        </div>
      `;

  }catch(e){

    toast(e.message,true);

  }
}

async function adminOrders(){

  try{

    const d = await api(
      "/api/webapp/admin/orders"
    );

    document.getElementById("screen").innerHTML =
      `
        <h2 class="section-title">
          📦 Заказы
        </h2>

        <div class="list">

          ${
            d.orders
              .map(
                o =>
                  `
                    <div class="list-item">

                      <div class="grow">

                        <h3>
                          №${o.id}
                          ·
                          ${esc(o.product_name)}
                        </h3>

                        <div class="meta">
                          ${esc(o.customer_name)}
                          ·
                          ${esc(o.customer_phone)}
                        </div>

                        <div class="badge">
                          ${esc(o.status)}
                        </div>

                      </div>

                      <select
                        class="input"
                        style="width:auto"
                        onchange="adminOrderStatus(${o.id},this.value)"
                      >

                        ${
                          [
                            "new",
                            "confirmed",
                            "assembling",
                            "ready",
                            "shipping",
                            "completed",
                            "rejected"
                          ]
                            .map(
                              s =>
                                `
                                  <option
                                    value="${s}"
                                    ${
                                      s === o.status
                                        ? "selected"
                                        : ""
                                    }
                                  >
                                    ${s}
                                  </option>
                                `
                            )
                            .join("")
                        }

                      </select>

                    </div>
                  `
              )
              .join("")
          }

        </div>
      `;

  }catch(e){

    toast(e.message,true);

  }
}

async function adminOrderStatus(id,status){

  try{

    await api(
      `/api/webapp/admin/orders/${id}`,
      {
        method:"POST",
        body:JSON.stringify({status})
      }
    );

    toast("Статус изменён");

  }catch(e){

    toast(e.message,true);

  }
}

async function adminUsers(){

  const q = prompt(
    "Поиск пользователя:"
  );

  if(q === null){
    return;
  }

  try{

    const d = await api(
      `/api/webapp/admin/users?q=${encodeURIComponent(q)}`
    );

    document.getElementById("screen").innerHTML =
      `
        <h2 class="section-title">
          👥 Пользователи
        </h2>

        <div class="list">

          ${
            d.users
              .map(
                u =>
                  `
                    <div class="list-item">

                      <div class="grow">

                        <h3>
                          ${esc(u.name || "Без имени")}
                        </h3>

                        <div class="meta">
                          ID ${u.telegram_id}
                          ·
                          @${esc(u.username || "—")}
                        </div>

                        <div class="meta">
                          ${esc(u.birthdate || "")}
                          ·
                          ${esc(u.phone || "")}
                        </div>

                      </div>

                    </div>
                  `
              )
              .join("")
          }

        </div>
      `;

  }catch(e){

    toast(e.message,true);

  }
}

async function adminProducts(){

  try{

    const d = await api(
      "/api/webapp/admin/products"
    );

    document.getElementById("screen").innerHTML =
      `
        <h2 class="section-title">
          🛍 Каталог
        </h2>

        <div class="list">

          ${
            d.products
              .map(
                p =>
                  `
                    <div class="list-item">

                      <img
                        class="thumb"
                        src="${
                          p.image_file
                            ? "/static/images/" + esc(p.image_file)
                            : "/static/webapp/images/catalog.png"
                        }"
                      >

                      <div class="grow">

                        <h3>
                          ${esc(p.name)}
                        </h3>

                        <div class="price">
                          ${Number(
                            p.price_rub
                          ).toLocaleString("ru-RU")} ₽
                        </div>

                        <div class="meta">
                          ${
                            p.is_active
                              ? "Активен"
                              : "Скрыт"
                          }
                        </div>

                      </div>

                    </div>
                  `
              )
              .join("")
          }

        </div>
      `;

  }catch(e){

    toast(e.message,true);

  }
}

async function adminContent(){

  try{

    const d = await api(
      "/api/webapp/admin/content"
    );

    document.getElementById("screen").innerHTML =
      `
        <h2 class="section-title">
          📝 Прогнозы
        </h2>

        <div class="list">

          ${
            d.predictions
              .map(
                p =>
                  `
                    <div class="list-item">

                      <div class="grow">

                        <h3>
                          ${esc(
                            p.text.slice(0,100)
                          )}
                        </h3>

                        <div class="meta">
                          ${
                            p.is_active
                              ? "Опубликован"
                              : "Скрыт"
                          }
                        </div>

                      </div>

                    </div>
                  `
              )
              .join("")
          }

        </div>
      `;

  }catch(e){

    toast(e.message,true);

  }
}

async function adminMagic(){

  try{

    const d = await api(
      "/api/webapp/admin/magic8"
    );

    document.getElementById("screen").innerHTML =
      `
        <h2 class="section-title">
          🎱 Magic 8
        </h2>

        <div class="list">

          ${
            d.answers
              .map(
                a =>
                  `
                    <div class="list-item">

                      <div class="grow">

                        <h3>
                          ${esc(a.text)}
                        </h3>

                        <div class="meta">
                          ${
                            a.is_active
                              ? "Активен"
                              : "Скрыт"
                          }
                        </div>

                      </div>

                    </div>
                  `
              )
              .join("")
          }

        </div>
      `;

  }catch(e){

    toast(e.message,true);

  }
}

async function adminAudit(){

  try{

    const d = await api(
      "/api/webapp/admin/audit"
    );

    document.getElementById("screen").innerHTML =
      `
        <h2 class="section-title">
          🛡 Журнал
        </h2>

        <div class="list">

          ${
            d.rows
              .map(
                a =>
                  `
                    <div class="list-item">

                      <div class="grow">

                        <h3>
                          ${esc(a.action)}
                        </h3>

                        <div class="meta">
                          ${esc(
                            a.entity_type || ""
                          )}

                          ·

                          ${esc(
                            a.details || ""
                          )}
                        </div>

                      </div>

                    </div>
                  `
              )
              .join("")
          }

        </div>
      `;

  }catch(e){

    toast(e.message,true);

  }
}

function openModal(inner){

  document.getElementById(
    "modalRoot"
  ).innerHTML = `
    <div
      class="modal-backdrop"
      onclick="if(event.target===this)closeModal()"
    >

      <div class="modal">
        ${inner}
      </div>

    </div>
  `;
}

function closeModal(){
  document.getElementById(
    "modalRoot"
  ).innerHTML = "";
}

function toast(msg,bad=false){

  const t = document.getElementById("toast");

  t.textContent = msg;

  t.classList.add("show");

  setTimeout(
    () => t.classList.remove("show"),
    2300
  );
}

function val(id){
  return document
    .getElementById(id)
    ?.value
    ?.trim() || "";
}

function sleep(ms){
  return new Promise(
    r => setTimeout(r,ms)
  );
}

function esc(v){

  return String(v ?? "")
    .replace(
      /[&<>"']/g,
      m =>
        ({
          "&":"&amp;",
          "<":"&lt;",
          ">":"&gt;",
          "\"":"&quot;",
          "'":"&#039;"
        }[m])
    );
}

window.openReview = openReview;
window.submitReview = submitReview;
window.render = render;
window.renderForecast = renderForecast;
window.renderCatalog = renderCatalog;
window.renderNotifications = renderNotifications;
window.renderProfile = renderProfile;
window.go = go;
window.startPersonal = startPersonal;
window.renderSpheres = renderSpheres;
window.askMagic = askMagic;
window.submitMagic = submitMagic;
window.showProduct = showProduct;
window.startOrder = startOrder;
window.submitOrder = submitOrder;
window.saveSubscription = saveSubscription;
window.disableSubscription = disableSubscription;
window.editProfile = editProfile;
window.saveProfile = saveProfile;
window.deleteMyData = deleteMyData;
window.showMyDay = showMyDay;
window.showGift = showGift;
window.sendGift = sendGift;
window.renderDocs = renderDocs;
window.openDoc = openDoc;
window.showConsent = showConsent;
window.acceptConsent = acceptConsent;
window.closeModal = closeModal;
window.renderAdmin = renderAdmin;
window.adminOrders = adminOrders;
window.adminOrderStatus = adminOrderStatus;
window.adminUsers = adminUsers;
window.adminProducts = adminProducts;
window.adminContent = adminContent;
window.adminMagic = adminMagic;
window.adminAudit = adminAudit;

init();
