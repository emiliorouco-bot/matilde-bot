import os
import logging
import json
import re
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --- CONFIGURACIÓN ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- INICIALIZAR GEMINI ---
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

# --- CONECTAR A GOOGLE SHEETS ---
def get_sheet():
    try:
        creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client.open_by_key(SPREADSHEET_ID)
    except Exception as e:
        logger.error(f"Error conectando a Sheets: {e}")
        return None

def get_data_summary():
    """Obtiene un resumen de los datos de la planilla."""
    try:
        sheet = get_sheet()
        if not sheet:
            return "No se pudo conectar a la planilla."
        
        # Leer primera hoja
        ws = sheet.get_worksheet(0)
        all_data = ws.get_all_records()
        
        if not all_data:
            return "La planilla está vacía."
        
        # Convertir a texto para que Gemini lo entienda
        headers = list(all_data[0].keys()) if all_data else []
        rows_preview = all_data[:50]  # Máximo 50 filas para no exceder límites
        
        summary = f"La planilla tiene {len(all_data)} filas.\n"
        summary += f"Columnas: {', '.join(headers)}\n\n"
        summary += "Datos:\n"
        for row in rows_preview:
            summary += str(row) + "\n"
        
        return summary
    except Exception as e:
        logger.error(f"Error leyendo datos: {e}")
        return f"Error leyendo la planilla: {str(e)}"

def add_expense(description, amount, category=""):
    """Agrega un gasto a la planilla."""
    try:
        from datetime import date
        sheet = get_sheet()
        if not sheet:
            return False, "No se pudo conectar a la planilla."
        
        ws = sheet.get_worksheet(0)
        today = date.today().strftime("%d/%m/%Y")
        
        # Agregar fila al final
        ws.append_row([today, description, amount, category, "gasto"])
        return True, f"Gasto registrado: {description} - ${amount}"
    except Exception as e:
        logger.error(f"Error agregando gasto: {e}")
        return False, f"Error al registrar: {str(e)}"

# --- PROCESAR MENSAJE CON GEMINI ---
def process_with_gemini(user_message, data_context):
    prompt = f"""Sos el asistente de Matilde Backeri, una cafetería/panadería en La Plata.

Tenés acceso a estos datos de la planilla:
{data_context}

El usuario preguntó: "{user_message}"

Tu tarea:
1. Si pregunta por datos (ventas, gastos, totales, resúmenes), analizá los datos y respondé con los números concretos.
2. Si quiere REGISTRAR un gasto, respondé SOLO con este JSON exacto:
   {{"accion": "registrar_gasto", "descripcion": "descripción", "monto": 1234, "categoria": "categoría"}}
3. Si la pregunta no tiene que ver con datos del negocio, respondé brevemente que solo manejás datos de Matilde.

Respondé en español, de forma concisa y clara. Si son números, presentalos de forma ordenada.
Si necesitás registrar un gasto, responde SOLO el JSON, sin texto adicional."""

    try:
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        logger.error(f"Error con Gemini: {e}")
        return "Hubo un error procesando tu consulta."

# --- HANDLERS DE TELEGRAM ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🥐 *Hola! Soy el asistente de Matilde Backeri*\n\n"
        "Puedo ayudarte a:\n"
        "• Consultar ventas y gastos\n"
        "• Ver totales por período\n"
        "• Registrar nuevos gastos\n\n"
        "Escribime lo que necesitás en lenguaje natural.\n"
        "_Ej: '¿Cuánto vendimos esta semana?' o 'Anotá un gasto de $5000 en harina'_",
        parse_mode="Markdown"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    chat_id = update.message.chat_id
    
    # Mostrar "escribiendo..."
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    
    # Obtener datos de la planilla
    data_context = get_data_summary()
    
    # Procesar con Gemini
    response = process_with_gemini(user_message, data_context)
    
    # Verificar si es una acción de registrar gasto
    try:
        # Intentar extraer JSON si hay instrucción de registrar
        json_match = re.search(r'\{.*"accion".*\}', response, re.DOTALL)
        if json_match:
            action_data = json.loads(json_match.group())
            if action_data.get("accion") == "registrar_gasto":
                success, msg = add_expense(
                    action_data.get("descripcion", ""),
                    action_data.get("monto", 0),
                    action_data.get("categoria", "")
                )
                if success:
                    await update.message.reply_text(f"✅ {msg}")
                else:
                    await update.message.reply_text(f"❌ {msg}")
                return
    except (json.JSONDecodeError, AttributeError):
        pass
    
    # Respuesta normal
    await update.message.reply_text(response)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}")

# --- MAIN ---
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)
    
    logger.info("Bot iniciado...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
