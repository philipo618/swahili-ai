from django.urls import path
from . import views

app_name = 'chat'

urlpatterns = [
    path('', views.chat_home, name='chat_home'),
    path('send/', views.send_message, name='send_message'),
    path('upload/', views.upload_file, name='upload_file'),
    path('rename/<int:session_id>/', views.rename_chat, name='rename_chat'),
    path('delete/<int:session_id>/', views.delete_chat, name='delete_chat'),
    path('new/', views.new_chat, name='new_chat'),
    path('sample-questions/', views.sample_questions_api, name='sample_questions'),
]