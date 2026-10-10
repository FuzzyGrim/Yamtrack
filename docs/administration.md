# Administration

To access the admin page of the application, follow these steps:

## Enabling the Admin Interface

1. **Set Environment Variable**: You need to enable the admin interface by setting the environment variable `ADMIN_ENABLED` to `True`. This can be done in your environment configuration file (e.g., `.env` file) or directly in your `docker-compose.yml`.

      ```bash
      ADMIN_ENABLED=True
      ```

      For related settings, see [env-variables](env-variables.md).

2. **Access the Admin Page**: Once enabled, you can access the admin interface at the following URL:

      ```bash
      https://domain.com/admin/
      ```

## Logging In

To log in to the admin interface, you will need an admin account. You can either change an existing user to have admin privileges or create a new user with those roles.

### Changing an Existing User to Admin

1. **Open Django Shell**: If you are using Docker, you can access the Django shell by running the following command in your terminal:

      ```bash
      docker exec -it yamtrack python manage.py shell
      ```

2. **Set Admin Privileges**: Find the user you want to promote to admin. Replace `username` with the actual username of the user.

      ```python
      User.objects.filter(username='username').update(is_staff=True, is_superuser=True)
      ```

### Creating a New Admin User

1. **Open Django Shell**: As mentioned above, access the Django shell using Docker:

      ```bash
      docker exec -it yamtrack python manage.py shell
      ```

2. **Create a New User**: Use the following code to create a new user. Replace `new_username`, `new_password`, and `new_email` with the desired values.

      ```python
      User.objects.create_user(username='new_username', password='new_password', is_staff=True, is_superuser=True)
      ```

3. **Log In**: After creating the new user, you can log in to the admin interface using the new credentials.

### Admin Interface Overview

After logging in, you will see various tables representing the different models in your database:

Here, you can view and edit data. For example, to edit an item image:

![image](https://github.com/user-attachments/assets/8177cf5b-2f2b-4e51-b53e-003dd38c7503)

1. Click on the **Items** entry.
2. You will see a list of items. Click on the specific item you want to change.
3. In the item detail view, you can update the image URL in the corresponding field.
