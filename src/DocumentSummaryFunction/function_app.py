import azure.functions as func
import azure.durable_functions as df
import logging
from semantic_kernel.connectors.ai.open_ai import AzureChatCompletion
from semantic_kernel import Kernel
from semantic_kernel.connectors.ai.open_ai.prompt_execution_settings.azure_chat_prompt_execution_settings import (
    AzureChatPromptExecutionSettings,
)
from semantic_kernel.contents.chat_history import ChatHistory
import base64
import io
import fitz
from os import environ
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from urllib.parse import urlparse


myApp = df.DFApp(http_auth_level=func.AuthLevel.ANONYMOUS)

@myApp.blob_trigger(arg_name="myblob", path="load", connection="BlobTriggerConnection")
@myApp.durable_client_input(client_name="client")
async def blob_trigger_start(myblob: func.InputStream, client):
    logging.info(f"Python blob trigger function processed blob\n"
                    f"Name: {myblob.name}\n"
                    f"Blob Size: {myblob.length} bytes")
    
    # Read the blob content
    base64_bytes = base64.b64encode(myblob.read())

    # Start the Durable Functions orchestration
    instance_id = await client.start_new("document_orchestrator", None, {"filename":myblob.name,"data": base64_bytes.decode('ascii')})
    logging.info(f"Started orchestration with ID = '{instance_id}'.")


# Orchestrator
@myApp.orchestration_trigger(context_name="context")
def document_orchestrator(context):

    """
    Orchestrates multiple activities based on the input from the Blob trigger.
    """
    data_base64 = context.get_input()["data"]
    filename = context.get_input()["filename"]

    logging.info(f"File Name: {filename} ")
    logging.info(f"Data: {data_base64} ")

    

    # Validate the file extension
    if not filename.lower().endswith('.pdf'):
        return f"Skipping processing: {filename} is not a .pdf file."

    logging.info(f"****** Processing PDF Document *****")

    tasks = []

    base64_bytes = data_base64.encode('ascii')
    pdf_data = base64.b64decode(base64_bytes)
    pdf_file = io.BytesIO(pdf_data)
    doc = fitz.open(stream=pdf_file, filetype="pdf")

    logging.info(f"Pages : {doc.page_count} ")

    for page in doc:
        pagetext = page.get_text()
        tasks.append(context.call_activity("summarize_page", pagetext))

    results = yield context.task_all(tasks)

    summarized_pages = " ".join(results)

    logging.info(f"Summarized Pages : {summarized_pages} ")

    final_summary = yield context.call_activity("summarize_page", summarized_pages)

    logging.info(f"Summarized PDF : {final_summary} ")


    filename = filename.lstrip("load/")

    logging.info(f"File Name : {filename} ")

    tasks = []
    tasks.append(context.call_activity("move_blob_to_archive", {'filename':filename,'data':  data_base64}))
    tasks.append(context.call_activity("write_summary_to_blob", {'filename':filename,'finalsummary':  final_summary}))
 
    yield context.task_all(tasks)

    return final_summary

# Activity
@myApp.activity_trigger(input_name="pagetext")
async def summarize_page(pagetext: str):
    if len(pagetext) >0:
        return await chatCompletion(pagetext)
    else:
        return ""


@myApp.activity_trigger(input_name="resultdata")
def move_blob_to_archive(resultdata: str):
    try:
        credential = DefaultAzureCredential()
        account_url = environ["AZURE_STORAGE_URL"]
        blob_service_client = BlobServiceClient(account_url,credential)

        source_container = "load"
        archive_container = "archive"


        filename =resultdata['filename']
        data_base64 = resultdata['data']
        base64_bytes = data_base64.encode('ascii')
        pdf_data = base64.b64decode(base64_bytes)

        

        # Upload the content to the archive container
        archive_blob = blob_service_client.get_blob_client(container=archive_container, blob=filename)
        archive_blob.upload_blob(pdf_data, overwrite=True)  # Upload with overwrite option


        # Delete the original blob
        source_blob = blob_service_client.get_blob_client(container=source_container, blob=filename)
        source_blob.delete_blob()
        logging.info(f"Blob {filename} successfully deleted from load container.")

    except Exception as e:
        logging.error(f"Error moving blob to archive: {e}")
        raise e


@myApp.activity_trigger(input_name="resultdata")
def write_summary_to_blob(resultdata: str):
    try:

        credential = DefaultAzureCredential()
        account_url = environ["AZURE_STORAGE_URL"]
        blob_service_client = BlobServiceClient(account_url, credential)

        filename =resultdata['filename']
        final_summary = resultdata['finalsummary']

        filename_json = filename.split('.')[0] + '.json'

        # Define the output container and blob name
        output_container = "output"

        # Create a JSON document with the final summary
        import json
        summary_json = json.dumps({"filename":filename,  "summary": final_summary})

        # Upload the JSON document to the output container
        blob_client = blob_service_client.get_blob_client(container=output_container, blob=filename_json)
        blob_client.upload_blob(summary_json, overwrite=True)

        logging.info(f"Final summary successfully written to blob {filename} in 'output' container.")
    except Exception as e:
        logging.error(f"Error writing final summary to blob: {e}")
        raise e


async def chatCompletion(originaltext:str):


    credential = DefaultAzureCredential()

    # Set the API type to `azure_ad`
    environ["OPENAI_API_TYPE"] = "azure_ad"
    # Set the API_KEY to the token from the Azure credential
    token = credential.get_token("https://cognitiveservices.azure.com/.default").token
    environ["OPENAI_API_KEY"] = token

    environ["AZURE_OPENAI_AD_TOKEN"] = environ["OPENAI_API_KEY"]

    kernel = Kernel()

    # Add Azure OpenAI chat completion
    chat_completion = AzureChatCompletion(
        deployment_name=environ["AZURE_OPENAI_DEPLOYMENT"],
        api_key=environ["OPENAI_API_KEY"],
        endpoint=environ["AZURE_OPENAI_ENDPOINT"],
    )
    kernel.add_service(chat_completion)

    execution_settings = AzureChatPromptExecutionSettings()

    chat_history = ChatHistory()
    chat_history.add_system_message("""You are an AI assistant specializing in text summarization. Extract key information accurately and concisely while preserving the original meaning. 
                                     Adjust detail and tone based on user instructions, handling various text types such as articles, reports, and transcripts.""")
    
    chat_history.add_user_message(originaltext)

    result = await chat_completion.get_chat_message_content(
            chat_history=chat_history,
            settings=execution_settings,
            kernel=kernel,
        )


    return str(result)

