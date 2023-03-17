from abc import ABC, abstractmethod
import openai
# from transformers import pipeline

class APIHandler(ABC):
    @abstractmethod
    def chat(self, message):
        pass

    @abstractmethod
    def complete(self, prompt):
        pass



class OpenAIHandler(APIHandler):
    def __init__(self, conf):
        self.api_key = conf.get_openai_api_key()
        self.model = conf.get_openai_model()
        openai.api_key = self.api_key

    def chat(self, message):
        response = openai.Completion.create(
            engine=self.model,
            prompt=f"{message}\nAI:",
            temperature=0.8,
            max_tokens=150,
            top_p=1,
            frequency_penalty=0,
            presence_penalty=0,
            stop=["\n"]
        )
        return response.choices[0].text.strip()

    def complete(self, prompt):
        response = openai.Completion.create(
            engine=self.model,
            prompt=prompt,
            temperature=0.8,
            max_tokens=50,
            top_p=1,
            frequency_penalty=0,
            presence_penalty=0
        )
        return response.choices[0].text.strip()



# class HuggingFaceHandler(APIHandler):
#     def __init__(self, config_manager):
#         self.api_key = config_manager.get_huggingface_api_key()
#         self.model = config_manager.get_huggingface_model()
#         self.completion_pipeline = pipeline('text-generation', model=self.model)
#
#     def chat(self, message):
#         response = self.completion_pipeline(f"{message}\nAI:", max_length=150, do_sample=True, top_p=0.95)
#         return response[0]["generated_text"].split("\nAI:")[-1].strip()
#
#     def complete(self, prompt):
#         response = self.completion_pipeline(prompt, max_length=len(prompt) + 50, do_sample=True, top_p=0.95)
#         return response[0]["generated_text"][len(prompt):].strip()

    '''
    Please note that this example uses the local Hugging Face Transformers library to perform text completions, 
    which may require a powerful machine to run large models. If you want to use the Hugging Face API instead, you 
    can make HTTP requests to their API directly. For example, replace the complete method with the following code:
    '''
    # def complete(self, prompt):
    #     headers = {
    #         "Authorization": f"Bearer {self.api_key}"
    #     }
    #     data = {
    #         "inputs": prompt,
    #         "length": 50,
    #         "num_return_sequences": 1
    #     }
    #     response = requests.post(f"https://api-inference.huggingface.co/models/{self.model}", headers=headers,
    #                              json=data)
    #     response_json = response.json()
    #     return response_json[0]["generated_text"][len(prompt):].strip()
