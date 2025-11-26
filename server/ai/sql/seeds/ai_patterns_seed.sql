INSERT INTO ai_patterns(code,name,tier,score,description) VALUES
 ('best_one','The Best One','top',100,'Плавный старт → накопление → резкий памп и стабилизация'),
 ('rising_phoenix','Rising Phoenix','top',95,'Спад на старте, затем быстрое восстановление и рост'),
 ('wave_rider','Wave Rider','top',92,'Волнообразный рост с последовательно выше пиками'),
 ('clean_launch','Clean Launch','top',90,'Линейный стабильный рост без шума'),
 ('calm_storm','Calm Storm','top',88,'5–10с спокойствия, затем импульс без глубоких откатов'),
 ('gravity_breaker','Gravity Breaker','top',86,'Медленное накопление объёма → отрыв цены'),
 ('golden_curve','Golden Curve','top',85,'Параболический рост с снижением волатильности'),
 ('bait_switch','Bait & Switch','middle',60,'Быстрый памп в первые 5с → резкий дроп → лёгкий подъём'),
 ('echo_wave','Echo Wave','middle',58,'Повтор формы предыдущего пампа, но слабее'),
 ('flash_bloom','Flash Bloom','middle',55,'Мгновенный всплеск и откат'),
 ('tug_of_war','Tug of War','middle',52,'Чередующиеся скачки вверх/вниз, борьба сторон'),
 ('drunken_sailor','Drunken Sailor','middle',50,'Хаотичные движения без структуры'),
 ('ice_melt','Ice Melt','middle',48,'Постепенный спад после стабильного старта'),
 ('rug_prequel','Rug Prequel','bottom',20,'Стабильность 5–10с → обвал и исчезающая ликвидность'),
 ('death_spike','Death Spike','bottom',15,'Одиночный пик и мгновенное падение (honeypot/pump-trap)'),
 ('flatliner','Flatliner','bottom',10,'Плоская линия, «труп» токена'),
 ('smoke_bomb','Smoke Bomb','bottom',10,'Шумной рост на ботовых объёмах'),
 ('mirage_rise','Mirage Rise','bottom',8,'Плавный подъём при фейковых объёмах'),
 ('panic_sink','Panic Sink','bottom',5,'Резкая сброска объёма без причин'),
 ('black_hole','Black Hole','bottom',1,'Исчезновение цены и ликвидности одновременно')
ON CONFLICT (code) DO NOTHING;

