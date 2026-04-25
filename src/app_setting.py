from pydantic_settings import BaseSettings

class AppSettings(BaseSettings):
    OPENAI_API_KEY : str = "your-openai-api-key"
    MODEL_NAME : str = "gpt-4"
    TEMPERATURE : float = 0.7
    MAX_TOKENS : int = 1500
    context_builder_prompt_path: str = "context_builder_prompt.yml"
    text2sql_entity_extractor_prompt_path: str = "text2sql_entity_extractor_prompt.yml"
    text2sql_generator_prompt_path: str = "text2sql_generator.yml"
    suprivisor_prompt_path: str = "suprivisor_prompt.yml"
    response_generator_prompt_path: str = "response_generator_prompt.yml"
    fallback_prompt_path: str = "fallback_prompt.yml"
    sql_correction_prompt_path: str = "sql_correction.yml"
    test_data_generation_prompt_path: str = "test_data_generation_prompt.yml"
    test_data_validation_prompt_path: str = "test_data_validation_prompt.yml"
    db_path: str =  "orders.db"
    EMBED_MODEL: str = "text-embedding-3-small"
    customer_order_details_query: str = "SELECT DISTINCT oi.ordered_items FROM order_items AS oi JOIN orders AS o ON oi.order_id = o.order_id WHERE o.customer_id = ?;"
    customer_resturants_query: str = "SELECT DISTINCT restaurant_name FROM orders WHERE customer_id = ?;"
    customer_city_query: str = "SELECT DISTINCT restaurant_city FROM orders WHERE customer_id = ?;"
    customer_order_info_query: str = "SELECT DISTINCT oi.food_item, r.name AS restaurant_name, r.primary_cuisine AS cuisine, r.type AS restaurant_type FROM order_items oi JOIN orders o ON oi.order_id = o.order_id JOIN restaurants r ON o.restaurant_id = r.restaurant_id WHERE o.customer_id = ?;"
    order_item_name_query: str = "SELECT DISTINCT oi.food_item FROM order_items AS oi JOIN orders AS o ON oi.order_id = o.order_id WHERE o.customer_id = ?;"
    restaurant_name_query: str = "SELECT DISTINCT r.name FROM orders AS o JOIN restaurants AS r ON o.restaurant_id = r.restaurant_id WHERE o.customer_id = ?;"
    restaurant_city_query: str = "SELECT DISTINCT r.city FROM orders AS o JOIN restaurants AS r ON o.restaurant_id = r.restaurant_id WHERE o.customer_id = ?;"
    restaurant_type_query: str = "SELECT DISTINCT r.type FROM orders AS o JOIN restaurants AS r ON o.restaurant_id = r.restaurant_id WHERE o.customer_id = ?;"
    restaurant_cuisine_query: str = "SELECT DISTINCT r.primary_cuisine FROM orders AS o JOIN restaurants AS r ON o.restaurant_id = r.restaurant_id WHERE o.customer_id = ?;"
    FOOD_ITEMS: list = [
    "Potato Wedges", "Chicken Achari Kebab", "Kadhai Masala", "Fried Egg", "Dal Fry",
    "Corn Maggi", "Mango Ice Cream", "Ghee Roast Dosa", "Grass Hopper Blue Mocktail",
    "Veg Shezwan Fried Rice", "Paneer Manchurian Gravy", "Vegetable Sandwich",
    "Steamed Rice", "French Chicken", "Chicken Tikka Roll",
    "Homemade Chocolate Sauce Waffles", "Crumb Fried Fish",
    "Classic Chicken Burger", "Green Salad", "Chicken Biryani Combo",
    "Paneer Kadai", "Paneer Tikka Masala", "Aloo Do Pyaaza",
    "Amritsari Chole with Rice", "Veg Quesadillas", "Garlic Naan",
    "Tandoori Chicken"
    ]

    RESTAURANT_CITIES: list = [
    "Koramangala", "BTM", "Malleshwaram", "Ejipura", "Jayanagar",
    "Electronic City", "Whitefield", "Bannerghatta Road", "Richmond Road",
    "Kumaraswamy Layout", "Indiranagar", "Lavelle Road", "Cunningham Road",
    "Church Street", "MG Road", "Domlur", "Banashankari"
    ]

    RESTAURANT_NAMES: list = [
    "The Food Therapy", "Dhuaan", "Al-Marhaba Restaurant", "Stevie's", "Taipan",
    "Darbar", "Kholi Mane", "Hyderabad Biryaani House", "Kor Cafe",
    "Kitchen Garden", "Oldtown Dilli", "The Blue Wagon - Kitchen",
    "Smoke House Deli", "Bangalore Pub Exchange", "Cafe Velvette",
    "Indira Darshini", "Ambur Star Dum Biryani",
    "Tipsy Bull - The Bar Exchange", "Chels Cafe", "Tandoor Hut",
    "Currylicious", "Vyanjan", "Just Bake", "Maravanthe"
    ]

    RESTAURANT_TYPES: list = [
    "Casual Dining", "Quick Bites", "Cafe", "Bakery",
    "Dessert Parlor", "Fine Dining", "Pub",
    "Microbrewery", "Confectionery"
    ]

    RESTAURANT_CUISINES: list = [
    "North Indian", "Chinese", "South Indian", "Cafe", "Bakery",
    "Continental", "Mediterranean", "Italian", "European",
    "Thai", "Mexican"
    ]