"""Open-Meteo weather and geocoding; independently implemented."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location('weather_query_net', Path(__file__).with_name('_net.py'))
net = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(net)

WEATHER = {0:'晴', 1:'大部晴朗', 2:'局部多云', 3:'阴', 45:'雾', 48:'雾凇',
    51:'小毛毛雨', 53:'毛毛雨', 55:'强毛毛雨', 56:'冻毛毛雨', 57:'强冻毛毛雨',
    61:'小雨', 63:'中雨', 65:'大雨', 66:'冻雨', 67:'强冻雨', 71:'小雪',
    73:'中雪', 75:'大雪', 77:'雪粒', 80:'阵雨', 81:'中阵雨', 82:'强阵雨',
    85:'阵雪', 86:'强阵雪', 95:'雷暴', 96:'雷暴冰雹', 99:'强雷暴冰雹'}

def handle(text, context):
    net.begin()
    try:
        query = net.argument(text, {'/天气'}, 60)
        if query is None:
            return None
        cfg = net.settings(context)
        query = query or net.clean(cfg.get('default_city', ''), 60)
        if len(query) < 2:
            return '用法：/天气 北京（也可使用 Beijing、London 等地点名）'
        geo_params = {'name': query, 'count': 1, 'language': 'zh', 'format': 'json'}
        country = str(cfg.get('country_code', '')).strip().upper()
        if country:
            if len(country) != 2 or not country.isascii() or not country.isalpha():
                return '天气设置中的国家代码应为两位英文字母，例如 CN；留空可查全球。'
            geo_params['countryCode'] = country
        places = net.fetch('https://geocoding-api.open-meteo.com/v1/search', geo_params, ttl=300).get('results')
        if not isinstance(places, list) or not places:
            return '未找到这个地点。请补充省份/国家，或试试英文、拼音地点名；没有使用其他城市代替。'
        place = places[0]
        lat, lon = float(place['latitude']), float(place['longitude'])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError()
        weather = net.fetch('https://api.open-meteo.com/v1/forecast', {'latitude':lat,
            'longitude':lon, 'current':'temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m',
            'daily':'weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max',
            'timezone':'auto', 'forecast_days':3}, ttl=120)
        current, daily = weather['current'], weather['daily']
        label = ' / '.join(dict.fromkeys(net.clean(place.get(k, ''), 60) for k in ('name','admin1','country') if place.get(k)))
        lines = ['天气｜'+label, f"坐标：{lat:.3f}, {lon:.3f}",
            '数据时间：'+net.clean(current['time'])+'（'+net.clean(weather.get('timezone', '当地时区'))+'）',
            '当前：%s %s°C，湿度%s%%，风速%s km/h' % (
                WEATHER.get(current.get('weather_code'), '天气代码'+net.clean(current.get('weather_code'))),
                net.clean(current.get('temperature_2m','未知')), net.clean(current.get('relative_humidity_2m','未知')),
                net.clean(current.get('wind_speed_10m','未知'))), '未来3天：']
        for index, date in enumerate(daily['time'][:3]):
            lines.append('%s %s %s～%s°C，降水概率%s%%' % (net.clean(date),
                WEATHER.get(daily['weather_code'][index], '未知'), net.clean(daily['temperature_2m_min'][index]),
                net.clean(daily['temperature_2m_max'][index]), net.clean(daily['precipitation_probability_max'][index])))
        if len(daily['time']) < 3:
            lines.append('数据源未返回完整3天预报。')
        lines.append('来源：Open-Meteo（地名：GeoNames）；预报仅供参考。')
        return '\n'.join(lines)
    except Exception as exc:
        return net.failure('天气', exc)
