from airflow import DAG
from datetime import timedelta, datetime
from airflow.providers.http.sensors.http import HttpSensor
from airflow.providers.http.hooks.http import HttpHook
from airflow.operators.python import PythonOperator
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
import json
import pandas as pd
from airflow.providers.postgres.hooks.postgres import PostgresHook


def kelvin_to_fahrenheit(temp_in_kelvin):
    return (temp_in_kelvin - 273.15) * (9 / 5) + 32


# ---------- Replace SimpleHttpOperator with this ----------
def extract_weather_data(**context):
    http_hook = HttpHook(method='GET', http_conn_id='weathermap_api')
    response = http_hook.run(
        endpoint='/data/2.5/weather?q=Portland&APPID=24da2304743fc80db79e5dc167bc66a9'
    )
    data = response.json()
    context['ti'].xcom_push(key='weather_data', value=data)


def transform_load_data(task_instance):
    data = task_instance.xcom_pull(key='weather_data', task_ids='extract_weather_data')

    city = data["name"]
    weather_description = data["weather"][0]['description']
    temp_farenheit = kelvin_to_fahrenheit(data["main"]["temp"])
    feels_like_farenheit = kelvin_to_fahrenheit(data["main"]["feels_like"])
    min_temp_farenheit = kelvin_to_fahrenheit(data["main"]["temp_min"])
    max_temp_farenheit = kelvin_to_fahrenheit(data["main"]["temp_max"])
    pressure = data["main"]["pressure"]
    humidity = data["main"]["humidity"]
    wind_speed = data["wind"]["speed"]
    time_of_record = datetime.utcfromtimestamp(data['dt'] + data['timezone'])
    sunrise_time = datetime.utcfromtimestamp(data['sys']['sunrise'] + data['timezone'])
    sunset_time = datetime.utcfromtimestamp(data['sys']['sunset'] + data['timezone'])

    transformed_data = {
        "City": city,
        "Description": weather_description,
        "Temperature (F)": temp_farenheit,
        "Feels Like (F)": feels_like_farenheit,
        "Minimum Temp (F)": min_temp_farenheit,
        "Maximum Temp (F)": max_temp_farenheit,
        "Pressure": pressure,
        "Humidity": humidity,
        "Wind Speed": wind_speed,
        "Time of Record": time_of_record,
        "Sunrise (Local Time)": sunrise_time,
        "Sunset (Local Time)": sunset_time
    }

    df_data = pd.DataFrame([transformed_data])

    # ------------------ Save to S3 using S3Hook ------------------
    s3 = S3Hook(aws_conn_id='aws_default')  # Use the connection you set up in Airflow
    now = datetime.now()
    dt_string = now.strftime("%d%m%Y%H%M%S")
    file_name = f"current_weather_data_portland_{dt_string}.csv"
    s3.load_string(
        string_data=df_data.to_csv(index=False),
        key=f"weather_data/{file_name}",
        bucket_name="affan-openweather-project-s3",
        replace=True
    )

    # ------------------ Save to PostgreSQL ------------------
    postgres_hook = PostgresHook(postgres_conn_id='postgres_default')
    insert_query = """
        INSERT INTO weather_data (
            city, description, temperature_f, feels_like_f,
            min_temp_f, max_temp_f, pressure, humidity,
            wind_speed, time_of_record, sunrise, sunset
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    record_to_insert = (
        city, weather_description, temp_farenheit, feels_like_farenheit,
        min_temp_farenheit, max_temp_farenheit, pressure, humidity,
        wind_speed, time_of_record, sunrise_time, sunset_time
    )
    postgres_hook.run(insert_query, parameters=record_to_insert)


# ---------- DAG Definition ----------
default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'start_date': datetime(2023, 1, 8),
    'email': ['airflow@gmail.com'],
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 2,
    'retry_delay': timedelta(minutes=2)
}

with DAG(
    'weather_dag',
    default_args=default_args,
    schedule='@daily',
    catchup=False
) as dag:

    is_weather_api_ready = HttpSensor(
        task_id='is_weather_api_ready',
        http_conn_id='weathermap_api',
        endpoint='/data/2.5/weather?q=Portland&APPID=24da2304743fc80db79e5dc167bc66a9'
    )

    extract_weather_data = PythonOperator(
        task_id='extract_weather_data',
        python_callable=extract_weather_data
    )

    transform_load_weather_data = PythonOperator(
        task_id='transform_load_weather_data',
        python_callable=transform_load_data
    )

    is_weather_api_ready >> extract_weather_data >> transform_load_weather_data