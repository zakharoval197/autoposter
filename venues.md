# Реестр заведений для мониторинга афиши (venue_scraper.py)
*Обновлено: 2026-10-06. Проверка — curl с машины user (РФ).*

Парсинг: HTML страницы афиши → GigaChat извлекает события в JSON
(~3 с/заведение, бесплатно). Код: venue_scraper.py, список VENUES.
Выключить: AUTOPOSTER_VENUES=0 в .env.

## ✅ Подключены (живые, афиша извлекается)

| Заведение | Страница афиши | Тест 06.10 | Примечание |
|---|---|---|---|
| Клуб «16 Тонн» | https://www.16tons.ru/club/afisha | 3 события | HTML |
| Клуб Игоря Бутмана | https://moscow.butmanclub.ru/ | 8 событий | события на главной, /concerts/<slug> |
| Зарядье (зал) | https://zaryadyehall.ru/afisha | 7 событий | Битрикс, HTML |
| Козлов Клуб | https://kozlovclub.ru/ | 7 событий | Beget-кука (beget: True) |

## ⚠️ SPA — текста нет, LLM не из чего извлекать

Контент этих заведений приходит через Яндекс Афишу — не критично.

| Заведение | Сайт | Статус |
|---|---|---|
| Noor Bar | noorbar.com/events | Tilda, события грузятся JS — 0 извлечено |
| GAZ Clubare (ex-Газгольдер) | gaz.team/clubare | SPA — 0 извлечено |

## ❌ Мёртвое / переехало / не то (НЕ вносить)

| Что | Домен | Диагноз 06.10 |
|---|---|---|
| Gipsy | gipsymoscow.ru | домен захвачен (чужой сертификат); клуб закрыт, события исторически идут через Яндекс |
| Mutabor | mutabor.club | редирект на arma17.club, тот не открывается (000) |
| VK Stadium | vkstadium.ru | домен припаркован (reg.ru) — площадка закрыта |
| Propaganda | propagandamoscow.ru | DNS не резолвится |
| Powerhouse | powerhouse.moscow | DNS не резолвится |
| Pluton | pluton.moscow | DNS не резолвится |
| Симачев бар | simachbar.ru | DNS не резолвится |
| Правда | pravdaclub.ru | DNS не резолвится |
| Aglomerat | aglomerat.ru | connection reset (мёртв) |
| 1930 | 1930moscow.ru/.com | connection reset |
| Aurora Hall | aurorahall.ru | connection reset |
| Esse Jazz Club | essejazzclub.ru | DNS не резолвится |
| Известия Hall | izvestiahall.ru | DNS не резолвится |
| Crocus City Hall | crocus-hall.ru | сертификат протух (площадка закрыта) |
| Vegas City Hall | vegascityhall.ru | сертификат протух |
| Live Arena | live-arena.ru | 403 Cloudflare (нужен браузерный отпечаток) |
| Стрелка | strelka.com | сертификат untrusted root (verify=False поможет) |
| Флакон | flacon.ru | cert revocation offline (verify=False поможет) |
| stadium.ru | — | это СМИ «Российский стадион» про спорт, НЕ площадка |
| umclub.ru | — | домен занят детским сайтом «Умный малыш» (Union Bar ушёл) |
| rodnya.ru | — | это отель в Ялте! У студии Родня сайта с афишей нет (контент в Telegram) |

## Куда расти

- Telegram-каналы заведений (Gipsy, Mutabor, Родня, Сбой и др. живут там) —
  нужен Telethon + user-аккаунт. Отдельная задача.
- Стрелка/Флакон/Live Arena — добавить с verify=False или браузерным стеком.
- Стендап-клубы (StandUp Club #1, Standup Store) — не разведаны.
