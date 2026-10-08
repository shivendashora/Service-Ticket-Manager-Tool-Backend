import os
from typing import List

from jose import JWTError,jwt
from datetime import datetime, timedelta
from dotenv import load_dotenv
from passlib.context import CryptContext

from models.main_models import Forms, PopulatedFormData

load_dotenv()

secret_key = os.getenv("SECRET_KEY")
algorithm = os.getenv("algorithm")
token_expiry_time = os.getenv("token_expiry_time")
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto"
)


def hashed_password(password: str):
    return pwd_context.hash(password)


def verify_password(password: str, hashed_password: str):
    return pwd_context.verify(password, hashed_password)


def generate_token(user_id: int):
    payload = {
        "user_id": user_id,
        "exp": datetime.utcnow() + timedelta(minutes=int(token_expiry_time))
    }

    token = jwt.encode(
        payload,
        secret_key,
        algorithm=algorithm
    )

    return token

def parse_user_data(token: str):
    return jwt.decode(
        token,
        secret_key,
        algorithms=[algorithm]
    )

def parse_form_data(form_structure):
    def parse_inputs(inputs):
        parsed_inputs = []

        for input_field in inputs:
            parsed_input = {
                "input_id": input_field.input_id,
                "children": parse_inputs(input_field.children)
            }

            parsed_inputs.append(parsed_input)

        return parsed_inputs

    return parse_inputs(form_structure)


def parse_form_data(form_data: List[PopulatedFormData]):
    parsed_data = {}

    def parse_input(input_data: PopulatedFormData):
        parsed_data[input_data.input_id] = input_data.value

        for child in input_data.children:
            parse_input(child)

    for input_data in form_data:
        parse_input(input_data)

    return parsed_data
    


    

