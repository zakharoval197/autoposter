from dotenv import load_dotenv
load_dotenv('.env', override=True)

import generator

event = {
    'title': 'Ночь техно в «Мутаборе»',
    'venue': 'Мутабор, Артплей',
    'event_date': '2026-10-10',
    'event_time': '23:00',
    'price': '1500₽',
    'link': 'https://example.com/test',
    'source': 'TestSource',
    'description': 'A.Fruit, техно, визуалы'
}

skill = generator.load_skill()
print('=== СКИЛЛ ЗАГРУЖЕН ===')
print(skill[:300])
print('\n=== ГЕНЕРАЦИЯ ПОСТА ===')
text = generator.generate_post(event, skill)
print(text)
