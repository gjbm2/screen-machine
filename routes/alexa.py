from routes.utils import findfile
from routes.openai import openai_prompt
import json
import re

def Brianize(input_ssml):
    if "<voice" in input_ssml:
        return input_ssml
    return re.sub(
        r"<speak>(.*?)</speak>",
        r"<speak><voice name='Brian'>\1</voice></speak>",
        input_ssml,
        flags=re.DOTALL
    )

def is_image_generation_intent(nlp_output: dict) -> bool:
    return (
        nlp_output.get("status") == "specific_action" and
        isinstance(nlp_output.get("data"), dict) and
        nlp_output["data"].get("type") == "image_generation"
    )

def refine(input_prompt, system_prompt = "alexa-preprocess.txt"):
    response = openai_prompt(
        user_prompt=input_prompt,
        system_prompt=system_prompt,
        model_name="gpt-4o",
        upload=[
            "alexapreprocess.schema.json",
            "alexapersonality.txt"
        ]
    )
    
    #print(f"*** response: {response}")

    if isinstance(response, str):
        cleaned = re.sub(r"^```(?:json)?|```$", "", response.strip(), flags=re.MULTILINE).strip()
        try:
            response = json.loads(cleaned)
        except Exception as e:
            print("!!! Failed to parse JSON response:")
            print(cleaned)
            raise

    '''
    # Handle error or fallback cases
    if isinstance(response, dict):
        if "error" in response:
            return {
                "response_ssml": Brianize("<speak>Sorry, something went wrong while preparing the image.</speak>"),
                "ssml": "<speak>Sorry, something went wrong while preparing the image.</speak>",
                "nlp_confidence": 0.0
            }
        if "fallback" in response:
            fallback = response["fallback"]
            return {
                "response_ssml": Brianize(f"<speak>I couldn't fully parse it, but I understood: {fallback}</speak>"),
                "ssml": f"<speak>I couldn't fully parse it, but I understood: {fallback}</speak>",
                "nlp_confidence": 0.4
            }
        output_object = response  # Already parsed dict
    else:
        try:
            cleaned = re.sub(r"^```(?:json)?|```$", "", response.strip(), flags=re.MULTILINE).strip()
            output_object = json.loads(cleaned)
        except Exception as e:
            print("!!! Failed to parse JSON response:")
            print(response)
            print(e)
            return {
                "response_ssml": Brianize("<speak>Something went wrong while understanding your request.</speak>"),
                "ssml": "<speak>Something went wrong while understanding your request.</speak>",
                "nlp_confidence": 0.0
            }
    '''

    # Brianize spoken response
    response_ssml = response.get("response_ssml", "<speak>Sorry, I can't help with that.</speak>")
    response["response_ssml"] = Brianize(response_ssml)

    #print(f"*** response: {response}")
    #import os
    #os._exit(1)
    
    return response
