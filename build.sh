# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Django setup
python manage.py migrate
python manage.py collectstatic --noinput
