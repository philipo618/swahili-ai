from django import forms


class MessageForm(forms.Form):
    message = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={'rows': 3, 'placeholder': 'Type your message...'}),
    )
    session_id = forms.CharField(required=False, widget=forms.HiddenInput())
