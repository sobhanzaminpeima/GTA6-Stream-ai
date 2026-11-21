/* English / Persian (فارسی) UI. English strings in the templates are the keys; after every
   render the page's text nodes, placeholders and titles are swapped for the Persian text
   and the layout flips to right-to-left. Data (titles, chat, numbers) is never translated. */
const FA = {
  // navigation + titles
  "Overview": "نمای کلی", "Live Radar": "رادار لایو", "Streamers & Rights": "استریمرها و مجوزها", "Chat Intelligence": "تحلیل چت",
  "Viral Moments": "لحظه‌های وایرال", "AI Clip Studio": "استودیوی کلیپ هوشمند", "Content Calendar": "تقویم محتوا",
  "YouTube Analytics": "آمار یوتیوب", "Growth Intelligence": "تحلیل رشد", "Costs": "هزینه‌ها", "Settings & Status": "تنظیمات و وضعیت",
  "Growth Studio": "استودیو رشد", "Viral Clip Hunter": "شکارچی کلیپ وایرال",
  "Catch the Moment. Tell the Story. Grow the Channel.": "لحظه را بگیر. داستان را بگو. کانال را رشد بده.",
  "live": "آنلاین", "reconnecting…": "در حال اتصال دوباره…", "connecting…": "در حال اتصال…",
  // overview
  "▶ Run simulation (own footage)": "▶ اجرای شبیه‌سازی (ویدیوی خودتان)", "Simulate a stream without permission": "شبیه‌سازی استریم بدون مجوز",
  "Find live streams": "پیدا کردن استریم‌های لایو", "Monitored streams": "استریم‌های تحت نظر", "Chat messages / min": "پیام چت در دقیقه",
  "Viral candidates": "کاندیدهای وایرال", "Spent this month": "هزینه‌ی این ماه", "Live now": "الان لایو",
  "No stream is being monitored.": "هیچ استریمی تحت نظر نیست.", "Highest-scoring moments": "بهترین لحظه‌ها", "all →": "همه ←",
  "In production / ready for approval": "در حال ساخت / آماده‌ی تأیید", "Nothing waiting for your approval.": "چیزی منتظر تأیید شما نیست.",
  "Latest published Shorts": "آخرین شورت‌های منتشرشده", "No Shorts uploaded through the studio yet.": "هنوز شورتی از استودیو آپلود نشده.",
  "timeline →": "تایم‌لاین ←", "open →": "باز کردن ←", "← all projects": "→ همه‌ی پروژه‌ها",
  "— = not available from YouTube Analytics yet": "— یعنی هنوز از YouTube Analytics در دسترس نیست",
  // radar
  "Run discovery now": "جستجوی استریم‌ها", "Find free-to-reuse streams (CC BY)": "پیدا کردن استریم‌های آزاد برای استفاده (CC BY)", "Any license": "هر لایسنسی", "Free to reuse (CC BY)": "آزاد برای استفاده (CC BY)", "Needs permission": "نیاز به مجوز", "needs permission": "نیاز به مجوز", "CC BY · free to reuse": "CC BY · آزاد برای استفاده", "+ Add a stream manually": "+ افزودن دستی استریم", "Any": "همه", "Live now ": "الان لایو",
  "Offline": "آفلاین", "All platforms": "همه‌ی پلتفرم‌ها", "Simulation": "شبیه‌سازی", "Uploads": "آپلودها", "Any status": "هر وضعیتی",
  "Monitoring": "در حال پایش", "Buffering": "در حال ضبط بافر", "Idle": "بیکار", "Stopped": "متوقف", "Any rights": "هر مجوزی",
  "Owned": "مالکیت خودتان", "Licensed": "دارای لایسنس", "Authorized": "مجاز", "Restricted": "ممنوع", "Unknown": "نامشخص",
  "Any content": "هر محتوایی", "Confirmed gameplay": "گیم‌پلی تأییدشده", "GTA V gameplay": "گیم‌پلی GTA V",
  "GTA VI commentary": "تحلیل GTA VI", "Trailer reaction": "ری‌اکشن تریلر", "Fan concept": "کانسپت طرفداران", "Unverified": "تأییدنشده",
  "Monitor chat": "پایش چت", "Monitor + record": "پایش + ضبط", "Stop": "توقف", "source ↗": "منبع ↗", "Language (en)": "زبان (en)",
  "Min viewers": "حداقل بیننده", "Max viewers": "حداکثر بیننده", "Add a stream manually": "افزودن دستی استریم", "Add": "افزودن",
  "Platform": "پلتفرم", "Title": "عنوان", "Channel name": "نام کانال", "Source URL": "آدرس منبع", "Stream settings": "تنظیمات استریم", "Save": "ذخیره",
  // streamers
  "Add a streamer": "افزودن استریمر", "Channel URL, @handle or id": "آدرس کانال، @هندل یا شناسه", "Display name (optional)": "نام نمایشی (اختیاری)",
  "Priority": "اولویت", "Language": "زبان", "Content category": "دسته‌ی محتوا", "Add to watchlist": "افزودن به لیست پایش",
  "Record a permission": "ثبت مجوز", "For streamer": "برای استریمر", "…or one source URL": "…یا یک آدرس منبع", "State": "وضعیت",
  "Content owner": "مالک محتوا", "Evidence (link to email / contract / campaign terms)": "مدرک (لینک ایمیل / قرارداد / شرایط کمپین)",
  "Recording allowed": "ضبط مجاز است", "Editing allowed": "ویرایش مجاز است", "Commercial use (monetized)": "استفاده‌ی تجاری (درآمدزا)",
  "Attribution required": "ذکر منبع لازم", "Expires": "انقضا", "Save permission": "ذخیره‌ی مجوز", "Watchlist": "لیست پایش",
  "Streamer": "استریمر", "Category": "دسته", "Rights": "مجوز", "Candidates": "کاندیدها", "Auto-monitor": "پایش خودکار", "Keywords": "کلمات کلیدی",
  "Remove": "حذف", "Permission records": "سوابق مجوز", "Scope": "محدوده", "Owner": "مالک", "Evidence": "مدرک", "Delete": "حذف",
  "No permissions recorded.": "هیچ مجوزی ثبت نشده.",
  // chat
  "Stop monitoring": "توقف پایش", "Stream timeline": "تایم‌لاین استریم", "last 15 minutes · updates live": "۱۵ دقیقه‌ی اخیر · به‌روزرسانی زنده",
  "messages / s": "پیام در ثانیه", "reaction weight": "وزن واکنش", "anomaly score": "امتیاز ناهنجاری", "audio excitement": "هیجان صدا",
  "visual activity": "فعالیت تصویری", "candidate": "کاندید", "suppressed spike": "اسپایک ردشده", "Detections (with reasons)": "تشخیص‌ها (با دلیل)",
  // moments
  "Active": "فعال", "Min score": "حداقل امتیاز", "Any platform": "هر پلتفرمی", "Sort: score": "مرتب‌سازی: امتیاز", "Sort: newest": "مرتب‌سازی: جدیدترین",
  "⬆ Upload your own clip": "⬆ آپلود کلیپ خودتان", "Time": "زمان", "Stream": "استریم", "Moment": "لحظه", "Score": "امتیاز", "Confidence": "اطمینان",
  "Clip": "کلیپ", "Production": "تولید", "Open": "باز کردن", "Create Short": "ساخت شورت", "Why it scored": "چرا این امتیاز",
  "Timing": "زمان‌بندی", "Chat around the moment": "چت اطراف لحظه", "Notes": "یادداشت", "Save notes": "ذخیره‌ی یادداشت",
  "Save candidate": "ذخیره‌ی کاندید", "Dismiss": "رد کردن", "Open timeline": "باز کردن تایم‌لاین", "Review source ↗": "بررسی منبع ↗",
  "Discovery report": "گزارش کشف", "GENERATE SHORT": "ساخت شورت", "Save calibration": "ذخیره‌ی کالیبراسیون", "Copy": "کپی",
  "Editing preset": "قالب ادیت", "Cut": "برش", "Upload & extract": "آپلود و استخراج", "Video file": "فایل ویدیو",
  // studio
  "Preview": "پیش‌نمایش", "Commentary": "روایت", "Shorts editor": "ویرایشگر شورت", "Captions (SRT)": "زیرنویس (SRT)",
  "Quality checks": "بررسی کیفیت", "YouTube metadata": "متادیتای یوتیوب", "Approval & publishing": "تأیید و انتشار",
  "Before the moment (one sentence per line)": "قبل از لحظه (هر خط یک جمله)", "After the moment": "بعد از لحظه",
  "Save script & re-voice changed lines": "ذخیره‌ی متن و صداگذاری خطوط تغییرکرده", "Manual trim (source seconds)": "برش دستی (ثانیه‌ی منبع)",
  "Preset": "قالب", "Apply & re-render": "اعمال و رندر دوباره", "Regenerate one stage (earlier stages are kept)": "ساخت دوباره‌ی یک مرحله (مراحل قبلی حفظ می‌شوند)",
  "Regenerate": "ساخت دوباره", "Save corrections & re-burn": "ذخیره‌ی اصلاحات و رندر زیرنویس", "Description": "توضیحات",
  "Approve": "تأیید", "Request changes": "درخواست تغییر", "Reject": "رد", "Approve for visibility": "تأیید برای نمایش",
  "Private (default)": "خصوصی (پیش‌فرض)", "Unlisted": "غیرفهرست", "Public": "عمومی", "Prepare upload package": "آماده‌سازی پکیج آپلود",
  "Upload to YouTube": "آپلود در یوتیوب", "Privacy": "حریم نمایش", "Cancel": "لغو", "Rendering…": "در حال رندر…",
  "I watched the whole preview": "کل پیش‌نمایش را دیدم", "Title and cover match what the clip shows": "عنوان و کاور با محتوای کلیپ می‌خواند",
  "Footage rights and attribution are correct": "مجوز فوتیج و ذکر منبع درست است",
  // calendar / analytics / growth / costs
  "Plan a post": "برنامه‌ریزی پست", "When": "زمان", "Project": "پروژه", "Refresh from YouTube Analytics": "به‌روزرسانی از YouTube Analytics",
  "Short": "شورت", "Views": "بازدید", "Subs": "مشترک", "Likes": "لایک", "Comments": "کامنت", "Write this week's report": "نوشتن گزارش این هفته",
  "Top Shorts by views": "پربازدیدترین شورت‌ها", "Weakest retention": "ضعیف‌ترین ماندگاری", "Today": "امروز", "This month": "این ماه",
  "Avg per Short (actual)": "میانگین هر شورت (واقعی)", "YouTube quota today": "سهمیه‌ی یوتیوب امروز", "Usage this month": "مصرف این ماه",
  "Provider": "سرویس", "Units": "واحد", "Calls": "تعداد", "Cost": "هزینه",
  // settings
  "Connections": "اتصال‌ها", "real status, checked now": "وضعیت واقعی، همین الان بررسی شد", "Connect YouTube channel": "اتصال کانال یوتیوب",
  "API keys": "کلیدهای API", "saved only on this PC (local .env file), never shown again": "فقط روی همین کامپیوتر ذخیره می‌شود و دیگر نمایش داده نمی‌شود",
  "Opens Google sign-in; you choose the channel.": "صفحه‌ی ورود گوگل باز می‌شود و کانال را خودتان انتخاب می‌کنید.",
  "Tokens are stored encrypted on this PC.": "توکن‌ها رمزنگاری‌شده روی همین کامپیوتر ذخیره می‌شوند.",
  "No spikes yet. The baseline needs ~60 s of chat first.": "هنوز اسپایکی نیست. خط مبنا اول به حدود ۶۰ ثانیه چت نیاز دارد.",
  "Waiting for chat…": "منتظر چت…", "paste key": "کلید را بچسبانید", "Channel name ": "نام کانال", "Save keys": "ذخیره‌ی کلیدها", "Upload": "آپلود", "Automation": "اتوماسیون", "Save settings": "ذخیره‌ی تنظیمات",
  "Editing presets": "قالب‌های ادیت", "System status": "وضعیت سیستم", "Jobs": "کارها", "Audit log": "گزارش رویدادها",
  "Run data retention now": "پاک‌سازی داده‌های قدیمی", "Channel handle": "هندل کانال", "Narrator": "راوی", "Narration speed": "سرعت روایت",
  "Authorized recording on": "ضبط مجاز روشن", "Auto-generate drafts": "ساخت خودکار پیش‌نویس", "Allow paid Claude calls": "اجازه‌ی استفاده‌ی پولی از Claude",
  "Allow paid ElevenLabs calls": "اجازه‌ی استفاده‌ی پولی از ElevenLabs", "Notifications": "اعلان‌ها",
  // statuses (pills)
  "MONITORING": "در حال پایش", "BUFFERING": "در حال ضبط", "STOPPED": "متوقف", "IDLE": "بیکار", "CONNECTED": "متصل",
  "RECONNECTING": "اتصال دوباره", "CHAT UNAVAILABLE": "چت در دسترس نیست", "SOURCE RESTRICTED": "منبع محدود", "PROCESSING": "در حال پردازش",
  "ERROR": "خطا", "OWNED": "مالکیت", "EXPLICITLY LICENSED": "لایسنس صریح", "AUTHORIZED FOR CLIPPING": "مجاز برای کلیپ",
  "RESTRICTED": "ممنوع", "UNKNOWN": "نامشخص", "CONFIGURED": "تنظیم شده", "NOT CONFIGURED": "تنظیم نشده", "READY TO CONNECT": "آماده‌ی اتصال",
  "report only": "فقط گزارش", "clip ready": "کلیپ آماده", "awaiting post roll": "منتظر ادامه‌ی ویدیو", "extracting": "در حال استخراج",
  "new": "جدید", "saved": "ذخیره‌شده", "dismissed": "ردشده", "in production": "در حال ساخت", "ready for approval": "آماده‌ی تأیید",
  "approved": "تأییدشده", "published": "منتشرشده", "queued": "در صف", "running": "در حال اجرا", "done": "انجام شد", "failed": "ناموفق",
  "SIMULATION": "شبیه‌سازی", "SIM": "شبیه‌سازی", "set": "ثبت شده", "not set": "ثبت نشده", "missing": "ندارد", "recommended": "پیشنهادی",
  "Exceptional candidate": "کاندید استثنایی", "High priority": "اولویت بالا", "Review recommended": "بررسی شود", "Low priority": "اولویت پایین",
};
// help paragraphs (matched by their first words)
const FA_HELP = [
  ["Hi! This studio", "سلام! این استودیو چت لایوهای GTA را زیر نظر می‌گیرد، لحظه‌هایی که بیننده‌ها هیجان‌زده می‌شوند را پیدا می‌کند و از فوتیج <b>مجاز</b> با روایت خودتان شورت می‌سازد. بدون مجوز هیچ چیزی ضبط نمی‌شود و بدون تأیید شما هیچ چیزی آپلود نمی‌شود. تازه شروع کرده‌اید؟ <b>اجرای شبیه‌سازی</b> را بزنید تا کل مسیر را ببینید."],
  ["Discovery uses", "جستجو از API رسمی یوتیوب و Twitch استفاده می‌کند. عنوانی که «GTA 6» دارد دلیل گیم‌پلی واقعی GTA 6 نیست؛ فقط دسته‌بندی پلتفرم یا تأیید شما آن را <i>گیم‌پلی تأییدشده</i> می‌کند. پایش چت استریم‌های عمومی مجاز است؛ ضبط نیاز به سابقه‌ی مجوز دارد."],
  ["Adding a streamer", "افزودن استریمر به لیست پایش <b>هرگز</b> ضبط را شروع نمی‌کند. پایش چت و ضبط ویدیو دو مجوز جدا هستند. مجوز را با مدرک (ایمیل، قرارداد، شرایط کمپین) و تاریخ انقضا ثبت کنید. داشتن مجوز به معنی تضمین درآمد نیست."],
  ["Scores are editorial", "امتیازها تخمین ویرایشی (۰ تا ۱۰۰) هستند، نه پیش‌بینی بازدید. امتیازی که فقط از چت می‌آید با برچسب <i>اطمینان کمتر</i> نشان داده می‌شود. لحظه‌های استریم‌های بدون مجوز فقط یک <b>گزارش با زمان دقیق</b> و پیش‌نویس درخواست مجوز می‌شوند و هیچ فوتیجی برداشته نمی‌شود."],
  ["Chat analysis", "تحلیل چت، تشخیص اسپایک، سیگنال‌های صدا و تصویر، برش و رندر همه روی کامپیوتر خودتان و رایگان انجام می‌شود. استفاده‌های پولی (روایت Claude، صدای ElevenLabs) فقط وقتی در تنظیمات روشنشان کنید و در محدوده‌ی بودجه انجام می‌شوند."],
  ["Rhythm over volume", "ریتم مهم‌تر از تعداد است: روزی حدود یک شورت و هفته‌ای ۱ تا ۲ ویدیوی بلند. این تقویم برنامه‌ی شماست؛ زمان‌بندی واقعی یوتیوب هنگام آپلود انجام می‌شود."],
  ["Watch the preview first", "اول پیش‌نمایش را کامل ببینید. تأیید به‌صورت پیش‌فرض فقط آپلود <b>خصوصی</b> را مجاز می‌کند؛ عمومی کردن همیشه تأیید جداگانه می‌خواهد."],
];

const I18N = {
  lang: (() => { const q = new URLSearchParams(location.search).get("lang"); if (q === "fa" || q === "en") return q;
    try { return localStorage.getItem("studio_lang") || ((navigator.language || "").startsWith("fa") ? "fa" : "en"); } catch { return "en"; } })(),
  set(l) { this.lang = l; try { localStorage.setItem("studio_lang", l); } catch {} this.apply(); },
  apply(root = document.body) {
    const fa = this.lang === "fa";
    document.documentElement.lang = fa ? "fa" : "en";
    document.documentElement.dir = fa ? "rtl" : "ltr";
    const btn = document.getElementById("lang-btn"); if (btn) btn.textContent = fa ? "English" : "فارسی";
    if (!fa) return;
    root.querySelectorAll(".help").forEach((el) => {
      if (el.dataset.tr) return; const txt = el.textContent.trim();
      const hit = FA_HELP.find(([k]) => txt.startsWith(k)); if (hit) { el.innerHTML = hit[1]; el.dataset.tr = "1"; }
    });
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, { acceptNode: (n) => (n.parentElement && ["SCRIPT", "STYLE", "TEXTAREA"].includes(n.parentElement.tagName) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT) });
    const nodes = []; while (walker.nextNode()) nodes.push(walker.currentNode);
    for (const n of nodes) {
      const raw = n.nodeValue, t = raw.trim(); if (!t) continue;
      const tr = FA[t] ?? FA[t.toUpperCase()] ?? null;
      if (tr) n.nodeValue = raw.replace(t, tr);
    }
    root.querySelectorAll("[placeholder],[title]").forEach((el) => {
      for (const a of ["placeholder", "title"]) { const v = el.getAttribute(a); if (v && FA[v.trim()]) el.setAttribute(a, FA[v.trim()]); }
    });
    root.querySelectorAll("option").forEach((o) => { const v = o.textContent.trim(); if (FA[v]) o.textContent = FA[v]; });
  },
};
// re-translate after views re-render: batched (one pass per 60 ms) and with the observer
// disconnected while we edit the DOM, so our own changes can never retrigger it
I18N._obs = new MutationObserver(() => {
  if (I18N.lang !== "fa" || I18N._timer) return;
  I18N._timer = setTimeout(() => {
    I18N._timer = null;
    I18N._obs.disconnect();
    try { I18N.apply(document.body); } finally { I18N._obs.observe(document.body, { childList: true, subtree: true }); }
  }, 60);
});
I18N._obs.observe(document.body, { childList: true, subtree: true });
