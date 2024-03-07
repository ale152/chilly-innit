import serial
import datetime
import sqlite3

ser = serial.Serial('/dev/serial0', baudrate=9600)

# # Check if database exists already, if not create a new one.
# year = datetime.date.today().year
# db_name = f'data/weather_{year}.db'
# conn = sqlite3.connect(db_name)
#
# cursor = conn.cursor()
# cursor.execute('''
#     CREATE TABLE IF NOT EXISTS weather_data (
#         timestamp TIMESTAMP,
#         sensor TEXT
#     )
# ''')
# conn.commit()


try:
    while True:
        # Read a line from the serial port
        try:
            raw_data = ser.readline().decode('utf-8').strip()
        except:
            print('pass')
            continue

        # # timestamp = datetime.localtime()
        # cursor.execute('INSERT INTO weather_data (timestamp, sensor) VALUES (CURRENT_TIMESTAMP, ?)', (raw_data))
        # conn.commit()




        wind_dir = float(data.split('c')[1].split('s')[0]) # degree
        wind_speed = float(data.split('s')[1].split('g')[0]) / 1.151 # miles/hour  --> Knots
        wind_gust = float(data.split('g')[1].split('t')[0])  / 1.151 # miles/hour  --> Knots
        temp = (float(data.split('t')[1].split('r')[0]) - 32) * 5/9 # Fahrenheit --> Celsius
        rain_hour = float(data.split('r')[1].split('p')[0]) * 25.40 / 100 # 0.01 inches --> mm
        rain_day = float(data.split('p')[1].split('h')[0]) * 25.40 / 100 # 0.01 inches --> mm
        humidity = float(data.split('h')[1].split('b')[0]) # Percent
        pressure = float(data.split('b')[1].split('*')[0]) / 10 # 0.1 hpa --> mmhp
        # Print the received data
        print(f'Received: {data}')
        print(f'Wind: {wind_speed} kn (gust {wind_gust} kn) from {wind_dir}')
        print(f'Temperature: {temp} C')
        print(f'Humidity: {humidity}%')
        print(f'Rain: {rain_hour} (last hour), {rain_day} (last 24 h)')
        print(f'Pressure: {pressure} hPa')

except KeyboardInterrupt:
    # Close the serial port on Ctrl+C
    ser.close()
    print("Serial port closed.")
