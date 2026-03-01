from flask import Blueprint, jsonify, request, send_file
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import requests
from config import API_URL
import os
from datetime import datetime

schedule_bp = Blueprint('schedule', __name__)
limiter = Limiter(
    app=None,
    key_func=get_remote_address,
    default_limits=["10 per second"]
)

@schedule_bp.route('/api/user/<int:user_id>')
@limiter.limit("10 per second")
def get_user(user_id):
    """
    Получение данных пользователя
    ---
    tags:
      - Расписание
    parameters:
      - name: user_id
        in: path
        type: integer
        required: true
        description: ID пользователя
    responses:
      200:
        description: Данные пользователя успешно получены
        schema:
          type: object
          properties:
            user_id:
              type: integer
              description: ID пользователя
            role:
              type: string
              description: Роль пользователя
            name_or_group:
              type: string
              description: Имя или группа пользователя
            is_class_teacher:
              type: boolean
              description: Является ли классным руководителем
            class_group:
              type: string
              description: Классная группа
      404:
        description: Пользователь не найден
      500:
        description: Ошибка сервера
    """
    try:
        response = requests.get(f"{API_URL}/user/{user_id}")
        return jsonify(response.json()), response.status_code
    except Exception as e:
        return jsonify({"error": "Ошибка сервера"}), 500

@schedule_bp.route('/api/schedule/user/<int:user_id>', methods=['POST'])
@limiter.limit("10 per second")
def get_user_schedule(user_id):
    """
    Получение расписания пользователя
    ---
    tags:
      - Расписание
    parameters:
      - name: user_id
        in: path
        type: integer
        required: true
        description: ID пользователя
      - name: body
        in: body
        required: true
        schema:
          type: object
          required: ["date"]
          properties:
            date:
              type: string
              pattern: "^\\d{2}\\.\\d{2}\\.\\d{4}$"
              description: Дата в формате DD.MM.YYYY
              example: "22.04.2025"
    responses:
      200:
        description: Расписание успешно получено
        schema:
          type: object
          properties:
            group:
              type: string
              description: Название группы
            teacher:
              type: string
              description: Имя преподавателя
            date:
              type: string
              description: Дата расписания
            schedule:
              type: array
              items:
                type: object
                properties:
                  time:
                    type: string
                    description: Время занятия
                  subject:
                    type: string
                    description: Название предмета
                  room:
                    type: string
                    description: Номер аудитории
      400:
        description: Неверный формат даты (должен быть DD.MM.YYYY) или отсутствует параметр date
      404:
        description: Пользователь не найден
      500:
        description: Ошибка сервера
    """
    try:
        response = requests.post(
            f"{API_URL}/getUserSchedule/{user_id}",
            json=request.json,
            headers={'Content-Type': 'application/json'}
        )
        return jsonify(response.json()), response.status_code
    except Exception as e:
        return jsonify({"error": "Ошибка сервера"}), 500

@schedule_bp.route('/api/schedule/group', methods=['POST'])
@limiter.limit("10 per second")
def get_group_schedule():
    """
    Получение расписания группы
    ---
    tags:
      - Расписание
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required: ["group"]
          properties:
            group:
              type: string
              description: Название группы
            date:
              type: string
              pattern: "^\\d{2}\\.\\d{2}\\.\\d{4}$",
              description: Дата в формате DD.MM.YYYY
              example: "22.04.2025"
    responses:
      200:
        description: Расписание группы успешно получено
      404:
        description: Группа не найдена в расписании
      500:
        description: Ошибка сервера
    """
    try:
        response = requests.post(
            f"{API_URL}/schedule/group",
            json=request.json,
            headers={'Content-Type': 'application/json'}
        )
        return jsonify(response.json()), response.status_code
    except Exception as e:
        return jsonify({"error": "Ошибка сервера"}), 500

@schedule_bp.route('/api/schedule/teacher', methods=['POST'])
@limiter.limit("10 per second")
def get_teacher_schedule():
    """
    Получение расписания преподавателя
    ---
    tags:
      - Расписание
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required: ["teacher"]
          properties:
            teacher:
              type: string
              description: Имя преподавателя
            date:
              type: string
              pattern: "^\\d{2}\\.\\d{2}\\.\\d{4}$",
              description: Дата в формате DD.MM.YYYY
              example: "22.04.2025"
    responses:
      200:
        description: Расписание преподавателя успешно получено
      404:
        description: Преподаватель не найден в расписании
      500:
        description: Ошибка сервера
    """
    try:
        response = requests.post(
            f"{API_URL}/schedule/teacher",
            json=request.json,
            headers={'Content-Type': 'application/json'}
        )
        return jsonify(response.json()), response.status_code
    except Exception as e:
        return jsonify({"error": "Ошибка сервера"}), 500

@schedule_bp.route('/api/schedule/audience', methods=['POST'])
@limiter.limit("10 per second")
def get_audience_schedule():
    """
    Получение расписания аудитории
    ---
    tags:
      - Расписание
    parameters:
      - name: body
        in: body
        required: true
        schema:
          type: object
          required: ["audience"]
          properties:
            audience:
              type: string
              description: Номер аудитории
            date:
              type: string
              pattern: "^\\d{2}\\.\\d{2}\\.\\d{4}$",
              description: Дата в формате DD.MM.YYYY
              example: "22.04.2025"
    responses:
      200:
        description: Расписание аудитории успешно получено
      404:
        description: Аудитория не найдена в расписании
      500:
        description: Ошибка сервера
    """
    try:
        response = requests.post(
            f"{API_URL}/schedule/audience",
            json=request.json,
            headers={'Content-Type': 'application/json'}
        )
        return jsonify(response.json()), response.status_code
    except Exception as e:
        return jsonify({"error": "Ошибка сервера"}), 500

@schedule_bp.route('/api/schedule/download/<schedule_type>/<date>')
@limiter.limit("10 per second")
def download_schedule(schedule_type, date):
    """
    Скачивание файла расписания
    ---
    tags:
      - Расписание
    parameters:
      - name: schedule_type
        in: path
        type: string
        required: true
        enum: [groups, teachers]
        description: Тип расписания
      - name: date
        in: path
        type: string
        required: true
        pattern: "^\\d{2}\\.\\d{2}\\.\\d{4}$"
        description: Дата в формате DD.MM.YYYY
    responses:
      200:
        description: Файл расписания успешно скачан
        schema:
          type: file
      400:
        description: Неверный тип расписания
      404:
        description: Файл не найден
      500:
        description: Ошибка сервера
    """
    try:
        if schedule_type not in ["groups", "teachers"]:
            return jsonify({"error": "Неверный тип расписания"}), 400

        response = requests.get(
            f"{API_URL}/schedule/download/{schedule_type}/{date}",
            stream=True
        )
        
        if response.status_code == 200:
            return send_file(
                response.raw,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                as_attachment=True,
                download_name=f"schedule_{schedule_type}_{date}.xlsx"
            )
        else:
            return jsonify({"error": "Файл не найден"}), 404
    except Exception as e:
        return jsonify({"error": "Ошибка сервера"}), 500

@schedule_bp.route('/api/groups')
@limiter.limit("10 per second")
def get_all_groups():
    """
    Получение списка всех групп
    ---
    tags:
      - Расписание
    responses:
      200:
        description: Список групп успешно получен
        schema:
          type: object
          properties:
            groups:
              type: array
              items:
                type: string
                description: Название группы
      404:
        description: Список групп не найден
      500:
        description: Ошибка сервера
    """
    try:
        response = requests.get(f"{API_URL}/groups")
        return jsonify(response.json()), response.status_code
    except Exception as e:
        return jsonify({"error": "Ошибка сервера"}), 500

@schedule_bp.route('/api/teachers')
@limiter.limit("10 per second")
def get_all_teachers():
    """
    Получение списка всех преподавателей
    ---
    tags:
      - Расписание
    responses:
      200:
        description: Список преподавателей успешно получен
        schema:
          type: object
          properties:
            teachers:
              type: array
              items:
                type: string
                description: ФИО преподавателя
      404:
        description: Список преподавателей не найден
      500:
        description: Ошибка сервера
    """
    try:
        response = requests.get(f"{API_URL}/teachers")
        return jsonify(response.json()), response.status_code
    except Exception as e:
        return jsonify({"error": "Ошибка сервера"}), 500 